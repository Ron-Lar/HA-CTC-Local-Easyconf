"""Coefficient of performance over a rolling year.

Modbus reports what the unit consumes but never what it delivers, so a real
coefficient of performance needs the display's lifetime counter for delivered
heat. The consumed side comes from the display's own counter where it has one,
as the i255 and the i550 Pro do, and otherwise from Modbus register 62341, which
holds the same number: 9166 kWh against the i255 display's 9166,0 on 2026-09-15.
CTC's manual for the i360 lists delivered energy on its stored operation data
page but no consumed energy at all.

Dividing those two gives the figure for the whole life of the machine, which
flatters or punishes it for years nobody is asking about. A yearly figure needs
the difference across a window, so one sample a day is kept and the oldest one
inside the window is used as the starting point. Until a year of samples exists
the lifetime figure is reported instead, and which of the two it is, is stated
rather than hidden.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Collection

from .const import (
    COP_HISTORY_DAYS,
    COP_WINDOW_DAYS,
    MODBUS_SENSORS,
    PERIOD_MARKERS,
    SENTINELS,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Below this the divisor is noise rather than a measurement. The two floors are
#: not the same kind of number, which is why they are so far apart. A lifetime
#: total is thousands of kilowatt hours, so rounding is irrelevant and the floor
#: only asks whether the machine has done anything at all: ten is enough, and
#: fifty, which this used to be, held back figures that were perfectly good. A
#: single day's delta is a handful of whole kilowatt hours, and since the display
#: counts in whole ones, two readings carry up to a kilowatt hour of rounding
#: between them. At three that is a third at worst, which is the most a daily
#: figure can carry and still mean something; at one it would be all of it.
MIN_CONSUMPTION_KWH = 10.0
MIN_CONSUMPTION_KWH_DAY = 3.0

#: A quotient outside this is not a performance figure. It is two counters that
#: do not belong together, or one of them standing still: a heat pump does not
#: deliver less than it is given, and nothing delivers ten times its input.
COP_MIN = 0.5
COP_MAX = 10.0

#: How long the unit must have been switched on before a counter standing at
#: zero is a fault rather than a machine that has not got going yet.
RUNNING_HOURS = 24

#: A sample has to be this old before it can serve as yesterday. The counters
#: are whole kilowatt hours, so a shorter span divides two small integers and
#: the answer swings wildly.
DAY_MIN_HOURS = 20
DAY_MAX_HOURS = 30

#: How long the short run of samples behind the daily figure is kept.
RECENT_DAYS = 4

#: A yearly figure has to stand on a span close to a year. A gap in the samples
#: must not quietly turn "the last year" into "the last fourteen months".
YEAR_MAX_DAYS = 380


@dataclass
class CopResult:
    """A coefficient of performance and how it was arrived at."""

    value: float | None
    #: "year" once a full window is available, "lifetime" before that.
    basis: str
    days: int
    energy_out: float | None = None
    energy_in: float | None = None

    def as_attributes(self) -> dict[str, Any]:
        basis = {
            "year": "rullande år",
            "day": "senaste dygnet",
            "first_year": "första året",
            "lifetime": "hela livslängden",
        }.get(self.basis, self.basis)
        return {
            "underlag": basis,
            "dygn i underlaget": self.days,
            "avgiven värme kWh": self.energy_out,
            "tillförd energi kWh": self.energy_in,
        }


def _parse(stamp: str) -> datetime:
    """Read a stored timestamp, treating a naive one as UTC."""
    value = datetime.fromisoformat(stamp)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def implausible(value: float | None) -> bool:
    """Whether a quotient is outside what a heat pump can actually do."""
    return value is not None and not (COP_MIN <= value <= COP_MAX)


def _ratio(out: float, consumed: float, floor: float = MIN_CONSUMPTION_KWH) -> float | None:
    if consumed < floor:
        return None
    value = round(out / consumed, 2)
    return None if implausible(value) else value


def lifetime_ratio(out: float | None, consumed: float | None) -> float | None:
    """The figure over the whole life, or nothing when the counters cannot carry one."""
    if out is None or consumed is None:
        return None
    return _ratio(out, consumed)


def powered_on_hours(runtime: Any) -> float | None:
    """The hours the unit says it has been switched on, if the page is harvested."""
    if getattr(runtime, "web", None) is None or not getattr(runtime, "operating_hours", None):
        return None
    data = runtime.web.data or {}
    readings = [data.get(value.key) for value in runtime.operating_hours]
    return max(
        (h for h in readings if isinstance(h, (int, float)) and not isinstance(h, bool) and h > 0),
        default=None,
    )


def counter_fault(
    out: float | None, consumed: float | None, hours: float | None
) -> str | None:
    """Which fault the two counters show, or None when they merely need time.

    ``stuck`` is a counter standing at zero on a unit that has been switched on
    for a day, which is what CTC's controllers do on installations where these
    counters are never written. ``implausible`` is two numbers whose quotient no
    heat pump could produce, so they are not the pair they are taken for. A new
    machine that has simply not counted far enough yet is neither.
    """
    if out is None or consumed is None:
        return None
    if hours is not None and hours >= RUNNING_HOURS and (out <= 0 or consumed <= 0):
        return "stuck"
    if consumed > 0 and implausible(round(out / consumed, 2)):
        return "implausible"
    return None


def cop_reason(
    value: float | None,
    basis: str,
    energy_out: float | None,
    energy_in: float | None,
    hours: float | None = None,
    modbus_answered: bool = True,
) -> str | None:
    """Why a figure is missing, in words the owner can act on or dismiss.

    An empty sensor that says nothing is the thing people ask about, so it says
    which of the reasons it is: no sample yet, too little energy so far, a
    counter standing still, or two counters that do not add up. Where the
    consumed side comes from Modbus, ``modbus_answered`` says whether register
    62341 has answered at all, so a model that lacks it is told so instead of
    waiting forever for a reading.
    """
    if value is not None:
        return None
    if energy_in is None and not modbus_answered:
        return "registret 62341 har inte svarat, så tillförd energi saknas"
    # Only no sample at all means the first year is unfinished: a sample with
    # nothing consumed in it is a counter that stood still for a year.
    if basis == "first_year" and energy_in is None:
        return "första året är inte fullt ännu"
    if energy_out is None or energy_in is None:
        if basis == "day":
            return "väntar på ett prov som är 20 till 30 timmar gammalt"
        return "räknarna har inte lästs"
    floor = MIN_CONSUMPTION_KWH_DAY if basis == "day" else MIN_CONSUMPTION_KWH
    too_little = f"för lite energi ännu, {energy_in:.1f} av {floor:.0f} kWh"
    if basis == "day":
        # A day's deltas are not the lifetime totals, so the lifetime rules do
        # not apply to them. Deltas of nothing are a day the compressor did not
        # run, not a counter the controller never writes, and below the floor
        # the pair is rounding noise, since the display counts whole kilowatt
        # hours and rounds the two counters independently: nothing is judged on
        # it. Above the floor a pair no heat pump could produce is still said so.
        if energy_in < floor:
            return too_little
        fault = counter_fault(energy_out, energy_in, None)
    else:
        fault = counter_fault(energy_out, energy_in, hours)
    if fault == "stuck":
        return "räknaren står på noll fast enheten varit igång, styrenheten fyller den inte"
    if fault == "implausible":
        return f"kvoten {round(energy_out / energy_in, 2)} är orimlig, räknarna hör inte ihop"
    if energy_in < floor:
        return too_little
    return None


class CopTracker:
    """Keeps one sample a day of the two lifetime counters."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._samples: dict[str, list[float]] = {}
        #: A short run of timestamped samples. The daily figure needs finer
        #: spacing than one a day, or "yesterday" could be anything from 12 to
        #: 36 hours ago depending on when the samples happened to land.
        self._recent: list[tuple[str, float, float]] = []
        #: The day the lifetime counters started from zero, when known. CTC's
        #: counters begin at nothing the day the unit is commissioned, which
        #: makes that day a sample of its own: zero delivered, zero consumed.
        self._anchor: str | None = None
        #: The first year of the machine's life, kept once it has been seen.
        self._first_year: list[float] | None = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        data = await self._store.async_load()
        if isinstance(data, dict) and isinstance(data.get("samples"), dict):
            self._samples = {
                day: [float(values[0]), float(values[1])]
                for day, values in data["samples"].items()
                if isinstance(values, (list, tuple)) and len(values) >= 2
            }
        if isinstance(data, dict):
            anchor = data.get("anchor")
            self._anchor = anchor if isinstance(anchor, str) else None
            first = data.get("first_year")
            if isinstance(first, (list, tuple)) and len(first) >= 3:
                self._first_year = [float(first[0]), float(first[1]), float(first[2])]
        if isinstance(data, dict) and isinstance(data.get("recent"), list):
            for row in data["recent"]:
                if isinstance(row, (list, tuple)) and len(row) >= 3:
                    try:
                        self._recent.append((str(row[0]), float(row[1]), float(row[2])))
                    except (TypeError, ValueError):
                        continue
        self._loaded = True

    async def async_record(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
        now: datetime | None = None,
    ) -> None:
        """Store the counters: one per day for the year, and a timestamped run."""
        if energy_out is None or energy_in is None:
            return
        await self.async_load()
        when = now or datetime.now(timezone.utc)
        stamp = (today or when.date()).isoformat()
        self._samples[stamp] = [float(energy_out), float(energy_in)]
        cutoff = ((today or when.date()) - timedelta(days=COP_HISTORY_DAYS)).isoformat()
        self._samples = {d: v for d, v in self._samples.items() if d >= cutoff}

        self._recent.append((when.isoformat(), float(energy_out), float(energy_in)))
        keep = when - timedelta(days=RECENT_DAYS)
        self._recent = [r for r in self._recent if _parse(r[0]) >= keep]

        self._capture_first_year(when.date())
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "samples": self._samples,
                "recent": self._recent,
                "anchor": self._anchor,
                "first_year": self._first_year,
            }
        )

    async def async_set_anchor(self, commissioned: date) -> None:
        """Record the day the counters started from zero.

        Only ever moved earlier, never later: the operating hours it is worked
        out from stop counting while the unit is switched off, so the earliest
        answer seen is the closest to the truth.
        """
        await self.async_load()
        stamp = commissioned.isoformat()
        if self._anchor is None or stamp < self._anchor:
            self._anchor = stamp
            await self._async_save()

    @property
    def anchor(self) -> date | None:
        return date.fromisoformat(self._anchor) if self._anchor else None

    def _capture_first_year(self, today: date) -> None:
        """Keep the first year's figure once a sample from its end exists.

        The counters were zero on the anchor day, so a sample taken about a year
        later holds the whole first year on its own. It is kept for good: the
        machine only ever has one first year.
        """
        if self._first_year is not None or self._anchor is None:
            return
        start = date.fromisoformat(self._anchor)
        for day in sorted(self._samples):
            span = (date.fromisoformat(day) - start).days
            if COP_WINDOW_DAYS <= span <= YEAR_MAX_DAYS:
                out, consumed = self._samples[day]
                self._first_year = [float(out), float(consumed), float(span)]
                return

    def result_first_year(self) -> CopResult:
        """The machine's first year, or nothing until it has had one."""
        if self._first_year is None:
            return CopResult(None, "first_year", 0)
        out, consumed, span = self._first_year
        return CopResult(_ratio(out, consumed), "first_year", int(span), round(out, 1), round(consumed, 1))

    def result_day(
        self,
        energy_out: float | None,
        energy_in: float | None,
        now: datetime | None = None,
    ) -> CopResult:
        """The figure over the last day, from the newest sample old enough to be
        yesterday. Nothing is returned until such a sample exists."""
        if energy_out is None or energy_in is None:
            return CopResult(None, "day", 0)
        when = now or datetime.now(timezone.utc)
        oldest_allowed = when - timedelta(hours=DAY_MAX_HOURS)
        newest_allowed = when - timedelta(hours=DAY_MIN_HOURS)
        window = [
            r for r in self._recent if oldest_allowed <= _parse(r[0]) <= newest_allowed
        ]
        if not window:
            return CopResult(None, "day", 0)
        stamp, base_out, base_in = max(window, key=lambda r: _parse(r[0]))
        delta_out = energy_out - base_out
        delta_in = energy_in - base_in
        if delta_out < 0 or delta_in < 0:
            return CopResult(None, "day", 0)
        hours = (when - _parse(stamp)).total_seconds() / 3600
        value = _ratio(delta_out, delta_in, MIN_CONSUMPTION_KWH_DAY)
        if value is None:
            return CopResult(None, "day", round(hours / 24), round(delta_out, 1), round(delta_in, 1))
        return CopResult(
            value,
            "day",
            max(1, round(hours / 24)),
            round(delta_out, 1),
            round(delta_in, 1),
        )

    def result(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """Work out the rolling figure, falling back to the lifetime one."""
        if energy_out is None or energy_in is None:
            return CopResult(None, "lifetime", 0)

        now = today or date.today()
        window_start = (now - timedelta(days=COP_WINDOW_DAYS)).isoformat()
        earliest = (now - timedelta(days=YEAR_MAX_DAYS)).isoformat()
        candidates = dict(self._samples)
        if self._anchor is not None:
            candidates.setdefault(self._anchor, [0.0, 0.0])
        older = sorted(d for d in candidates if earliest <= d <= window_start)
        if older:
            base_out, base_in = candidates[older[-1]]
            span = (now - date.fromisoformat(older[-1])).days
            delta_out = energy_out - base_out
            delta_in = energy_in - base_in
            # A counter that went backwards means the unit was replaced or reset;
            # the lifetime figure is the only honest answer then.
            if delta_out >= 0 and delta_in >= 0:
                value = _ratio(delta_out, delta_in)
                if value is not None:
                    return CopResult(value, "year", span, round(delta_out, 1), round(delta_in, 1))

        oldest = min(self._samples) if self._samples else None
        span = (now - date.fromisoformat(oldest)).days if oldest else 0
        return CopResult(
            _ratio(energy_out, energy_in),
            "lifetime",
            span,
            round(energy_out, 1),
            round(energy_in, 1),
        )


#: The older name of the delivered heat counter. The display's text catalogue
#: holds both generations (read off an i255 on 2026-09-15): text 935 "Energy
#: output (kWh)", in Swedish "Avgiven energi (kWh)", which is the row CTC's
#: manual shows on the i360's stored operation data page, beside 1808 "Energy
#: output total (kWh)" / "Avgiven värme totalt (kWh)" that the i255 shows. The
#: older generation has no consumed energy counter at all. The name is only
#: trusted on a row in kWh: 1807 "Energy output (kW)" is power, and sits on the
#: heat pump's operation data page.
_HEAT_NAMES = ("energy output", "avgiven energi")


def find_energy_totals(pages: list[Any]) -> tuple[Any | None, Any | None]:
    """Pick the two lifetime counters out of the harvested pages.

    Matched on the label the display itself printed, in English first and then
    in Swedish, so it works whichever language the integration reads the panel
    in. The newer names win wherever they appear on the page; only without them
    is the older name for delivered heat accepted. Consumed energy has no older
    name, and comes from Modbus instead where the display lacks it.
    """
    from .const import (
        LABEL_ENERGY_IN_EN,
        LABEL_ENERGY_IN_SV,
        LABEL_ENERGY_OUT_EN,
        LABEL_ENERGY_OUT_SV,
    )

    def label(value: Any) -> str:
        # The catalogue strips a trailing unit from the row name, so the stored
        # label is "Avgiven värme totalt" rather than "... (kWh)".
        return (value.label or "").strip().casefold()

    def exact(value: Any, english: str, swedish: str) -> bool:
        return label(value).startswith(english.casefold()) or label(value).startswith(
            swedish.casefold()
        )

    def loose(value: Any, names: tuple[str, ...]) -> bool:
        text = label(value)
        return (
            (getattr(value, "unit", None) or "").casefold() == "kwh"
            and text.startswith(names)
            and not any(marker in text for marker in PERIOD_MARKERS)
        )

    values = [value for page in pages for value in page.values]
    out = next(
        (v for v in values if exact(v, LABEL_ENERGY_OUT_EN, LABEL_ENERGY_OUT_SV)), None
    ) or next((v for v in values if loose(v, _HEAT_NAMES)), None)
    consumed = next(
        (v for v in values if exact(v, LABEL_ENERGY_IN_EN, LABEL_ENERGY_IN_SV)), None
    )
    return out, consumed


#: Modbus register 62341, the energy the compressor has consumed, in kWh.
MODBUS_CONSUMPTION_KEY = "compressor_kwh"
MODBUS_CONSUMPTION_ADDRESS = next(
    d.address for d in MODBUS_SENSORS if d.key == MODBUS_CONSUMPTION_KEY
)


def modbus_consumption_answered(answered: Collection[int] | None) -> bool:
    """Whether the controller has answered for register 62341 at all.

    Judged on the raw addresses the coordinator keeps in ``answered``, never on
    its decoded data. A key is left out of the data both when the block was
    silent this round and when the pair decoded to CTC's marker for a counter
    that is not fitted, so read off the data, a register that answered with the
    marker and a block that was quiet once would both pass for a register the
    model lacks. The set is cumulative over the run: answered means answered at
    least once since Home Assistant started, and a block the model lacks never
    joins it. Whether the answer is a number worth dividing by is a different
    question, which :func:`modbus_consumption` settles, and the two are kept
    apart on purpose: a register that answers zero is fitted and can be found
    to be stuck, while one that never answers has nothing to say about the
    machine.
    """
    return MODBUS_CONSUMPTION_ADDRESS in (answered or ())


def modbus_consumption(data: dict[str, Any] | None) -> float | None:
    """The consumed energy from Modbus, or None where there is no reading.

    Zero is a reading and is kept. A counter standing at zero on a unit that
    has been switched on for a day is the fault called ``stuck``, and the only
    way to find it is to carry the zero through to :func:`counter_fault`.
    Turning it into None here, as this once did, made such a machine look as
    if the register were missing: on an i360, whose display has no consumed
    energy counter, that meant no coefficient of performance sensors at all
    until somebody reloaded the entry after the counter had moved. None is for
    a register that was not read, a value that is not a number, and CTC's
    sentinels for "no sensor fitted"; a counter below zero is none of those
    and no reading either.
    """
    value = (data or {}).get(MODBUS_CONSUMPTION_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0 or value in SENTINELS:
        return None
    return float(value)


class ConsumptionSnapshot:
    """Modbus's consumed energy as it stood when the display was last read.

    The display is harvested every half hour by default and keeps its previous
    value when a cycle is skipped, while Modbus is read every thirty seconds.
    Dividing a stale delivered heat by a fresh consumption would move the daily
    figure by up to half an hour of compressor running, so the consumption is
    taken at the moment the delivered heat counter was actually read.
    """

    def __init__(self) -> None:
        self.value: float | None = None
        self._read_at: Any = None

    def update(self, read_at: Any, modbus_data: dict[str, Any] | None) -> None:
        """Take the Modbus reading if the display has been read since last time."""
        if read_at is None or read_at == self._read_at:
            return
        self._read_at = read_at
        # A Modbus value that is not usable right now must not be paired with
        # the new display reading either; better no sample than a wrong one.
        self.value = modbus_consumption(modbus_data)


def find_operating_hours(pages: list[Any]) -> Any | None:
    """Pick the unit's total powered-on hours out of the harvested pages.

    Two rows look alike, "Total drifttid" for the hours the unit has been
    powered and "Drifttid total" for the compressor alone, and in English they
    are both "Total operation time". Powered-on hours can never be fewer than
    compressor hours, so of the rows that match, the largest one is the right
    one; the choice is made at read time, from the values themselves.
    """
    matches = []
    for page in pages:
        for value in page.values:
            label = (value.label or "").strip().casefold()
            if (value.unit or "") != "h":
                continue
            if label.startswith("total drifttid") or label.startswith("total operation time"):
                matches.append(value)
    return matches or None



def current_totals(runtime: Any) -> tuple[float | None, float | None]:
    """The two lifetime counters as they stood when the display was last read.

    Consumption comes from the display's own counter where there is one, and
    otherwise from the Modbus reading taken when delivered heat was read.
    """
    if getattr(runtime, "web", None) is None:
        return None, None
    data = runtime.web.data or {}
    out = data.get(runtime.energy_out.key) if runtime.energy_out else None
    if runtime.energy_in is not None:
        consumed = data.get(runtime.energy_in.key)
    else:
        snapshot = getattr(runtime, "consumption_snapshot", None)
        consumed = snapshot.value if snapshot is not None else None
    return out, consumed


#: The coefficient of performance figures the report carries, by the name the
#: report builder takes them under. Returned as a mapping rather than a tuple:
#: adding a figure to a tuple silently breaks every caller that unpacks it, and
#: that once emptied a whole day's reports without a single error in sight.
COP_REPORT_KEYS = ("cop_day", "cop_year", "cop_first_year", "cop_lifetime")


def cop_for_report(runtime: Any) -> dict[str, float | None]:
    """Each figure only when it stands on its own span.

    Sending the lifetime figure under a yearly name would be a different number
    wearing the wrong label, so a figure without its span is left out.
    """
    figures: dict[str, float | None] = dict.fromkeys(COP_REPORT_KEYS)
    tracker = getattr(runtime, "cop", None)
    if tracker is None:
        return figures
    out, consumed = current_totals(runtime)
    yearly = tracker.result(out, consumed)
    figures["cop_year"] = yearly.value if yearly.basis == "year" else None
    figures["cop_day"] = tracker.result_day(out, consumed).value
    figures["cop_first_year"] = tracker.result_first_year().value
    if out is not None and consumed is not None and consumed >= MIN_CONSUMPTION_KWH:
        figures["cop_lifetime"] = round(out / consumed, 2)
    return figures
