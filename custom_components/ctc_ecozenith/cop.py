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
inside the window is used as the starting point. Each span stands on its own:
the lifetime figure is never dressed up as a year, and until a year of samples
exists the yearly figure says how far the samples have come instead of showing
nothing. The commissioning day, worked out from the powered-on hours, serves
the first year alone: the counters stood at zero that day, so a sample from
about a year later holds the whole first year.
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

#: The shorter windows, by the span the sensors report them under: how many
#: days back the base sample lies, and how many more days back it may lie
#: before the figure stops meaning what its name says. Read against the daily
#: samples the tracker already keeps for the year, so they cost nothing new.
#: A week may stretch to nine days and a month to thirty-five: a day or two of
#: missing samples, which a Home Assistant that was down over a weekend leaves
#: behind, should not cost the figure, but the yearly band's fifteen days would
#: turn a week into a fortnight.
WINDOWS: dict[str, tuple[int, int]] = {"week": (7, 2), "month": (30, 5)}


@dataclass
class CopResult:
    """A coefficient of performance and how it was arrived at."""

    value: float | None
    #: The span the figure stands on: "day", "week", "month", "year",
    #: "first_year" or "lifetime". Every method of the tracker answers under
    #: the span it was asked for, so a figure never wears another span's name.
    basis: str
    days: int
    energy_out: float | None = None
    energy_in: float | None = None
    #: What the tracker alone can say about an empty figure: how far its
    #: samples have come towards the window, or that the start is unknown.
    #: The counters' own reasons are :func:`cop_reason`'s business.
    reason: str | None = None

    def as_attributes(self) -> dict[str, Any]:
        basis = {
            "year": "rullande år",
            "day": "senaste dygnet",
            "week": "senaste 7 dygnen",
            "month": "senaste 30 dygnen",
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
    out: float | None,
    consumed: float | None,
    hours: float | None,
    floor: float = MIN_CONSUMPTION_KWH,
) -> str | None:
    """Which fault the two counters show, or None when they merely need time.

    ``stuck`` is a counter standing at zero on a unit that has been switched on
    for a day, which is what CTC's controllers do on installations where these
    counters are never written. ``implausible`` is two numbers whose quotient no
    heat pump could produce, so they are not the pair they are taken for. A new
    machine that has simply not counted far enough yet is neither.

    The quotient is judged only above the same floor :func:`_ratio` divides
    above. Below it the divisor is a kilowatt hour or two of whole-number
    rounding, and 11 against 1 is a quotient of nothing rather than a quotient
    of eleven: calling it a fault sent the two totals to the statistics
    backend for a machine that had merely started counting.
    """
    if out is None or consumed is None:
        return None
    if hours is not None and hours >= RUNNING_HOURS and (out <= 0 or consumed <= 0):
        return "stuck"
    if consumed >= floor and implausible(round(out / consumed, 2)):
        return "implausible"
    return None


def cop_reason(
    value: float | None,
    basis: str,
    energy_out: float | None,
    energy_in: float | None,
    hours: float | None = None,
    modbus_answered: bool = True,
    *,
    days: int = 1,
) -> str | None:
    """Why a figure is missing, in words the owner can act on or dismiss.

    An empty sensor that says nothing is the thing people ask about, so it says
    which of the reasons it is: no sample yet, too little energy so far, a
    counter standing still, or two counters that do not add up. Where the
    consumed side comes from Modbus, ``modbus_answered`` says whether register
    62341 has answered at all, so a model that lacks it is told so instead of
    waiting forever for a reading. For a week or a month ``days`` is the span
    the deltas cover, which sets their floor the way it sets the figure's.
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
    if basis == "day":
        floor = MIN_CONSUMPTION_KWH_DAY
    elif basis in WINDOWS:
        floor = MIN_CONSUMPTION_KWH_DAY * max(1, days)
    else:
        floor = MIN_CONSUMPTION_KWH
    too_little = f"för lite energi ännu, {energy_in:.1f} av {floor:.0f} kWh"
    if basis == "day" or basis in WINDOWS:
        # A day's deltas are not the lifetime totals, so the lifetime rules do
        # not apply to them. Deltas of nothing are a day the compressor did not
        # run, not a counter the controller never writes, and below the floor
        # the pair is rounding noise, since the display counts whole kilowatt
        # hours and rounds the two counters independently: nothing is judged on
        # it. Above the floor a pair no heat pump could produce is still said so.
        # A week or a month of deltas is the same kind of number, only larger.
        if energy_in < floor:
            return too_little
        fault = counter_fault(energy_out, energy_in, None, floor)
    else:
        fault = counter_fault(energy_out, energy_in, hours)
    if fault == "stuck":
        return "räknaren står på noll fast enheten varit igång, styrenheten fyller den inte"
    if fault == "implausible":
        return f"kvoten {round(energy_out / energy_in, 2)} är orimlig, räknarna hör inte ihop"
    if energy_in < floor:
        return too_little
    return None


#: The web coordinator's attribute for the moment of the last harvest that read
#: a page: CtcWebCoordinator.last_harvest, kept across a restart by the display
#: store. Read with a default so a test double without it still gets a notice:
#: the newest read_at then serves, which is the same moment to within seconds,
#: since read_at is only ever written by a read that worked.
LAST_HARVEST_ATTRIBUTE = "last_harvest"


def row_read_at(web: Any, key: str) -> datetime | None:
    """When the display row ``key`` was last read off the panel, or None.

    Through the coordinator's ``last_read`` where there is one; a double
    without it, as the sensor tests build, has never read anything.
    """
    read = getattr(web, "last_read", None)
    if not callable(read):
        return None
    moment = read(key)
    return moment if isinstance(moment, datetime) else None


def row_is_fresh(web: Any, key: str) -> bool | None:
    """Whether the row is fresh by the coordinator's own rule, or None if it cannot say."""
    fresh = getattr(web, "is_fresh", None)
    if not callable(fresh):
        return None
    return bool(fresh(key))


def stale_rows(web: Any, rows: Collection[Any]) -> list[tuple[Any, datetime]]:
    """The rows among ``rows`` that were read once and have since gone stale.

    A row that has never been read is not stale, it is unread, and the sensors
    say so in other words. Stale is the coordinator's judgement (R5): the
    page the row sits on has not been reached for as long as a whole harvest
    may fail in a row, which is when the row's own display sensor goes
    unavailable. Each stale row comes with the moment it was last read.
    """
    found: list[tuple[Any, datetime]] = []
    for row in rows:
        if row is None:
            continue
        read = row_read_at(web, row.key)
        if read is not None and row_is_fresh(web, row.key) is False:
            found.append((row, read))
    return found


def _row_name(row: Any) -> str:
    return (getattr(row, "label", None) or getattr(row, "key", None) or "?").strip()


def display_silence(web: Any, *rows: Any) -> str | None:
    """Why the display's figures are old: the reason text, or None while it answers.

    Two silences. After three failed harvests in a row the web coordinator
    reports failure and every display sensor goes unavailable; the figures
    that rest on stored samples stay available instead, and this is what they
    say about it, dated with the last harvest that read a page. Since R5 a
    page that is not reached is no failure of the harvest: the other pages
    are read, the coordinator reports success, and only that page's rows age
    in the data until their own sensors go unavailable. The counters the
    figures are read off are such rows, so they are handed in as ``rows``
    and a stale one gives the notice too, naming the row and dated with its
    own last read, instead of a figure read off a value nobody has read.
    """
    if web is None:
        return None
    if not getattr(web, "last_update_success", True):
        moment = getattr(web, LAST_HARVEST_ATTRIBUTE, None)
        if not isinstance(moment, datetime):
            stamps = [
                stamp for stamp in (getattr(web, "read_at", None) or {}).values()
                if isinstance(stamp, datetime)
            ]
            moment = max(stamps, default=None)
        if moment is None:
            return "displayen har inte svarat sedan Home Assistant startade"
        return f"displayen har inte svarat sedan {moment.isoformat(timespec='seconds')}"
    for row, read in stale_rows(web, rows):
        return (
            f"displayens rad {_row_name(row)} har inte lästs sedan "
            f"{read.isoformat(timespec='seconds')}"
        )
    return None


def reason_for(
    result: CopResult,
    out: float | None,
    consumed: float | None,
    hours: float | None = None,
    modbus_answered: bool = True,
    display: str | None = None,
) -> str | None:
    """Why the sensor for ``result`` shows nothing, the nearest cause first.

    A display that has gone quiet comes before everything, because every
    other answer would be read off counters that are no longer being read; the
    first year is the exception, since its figure is stored and the display
    cannot age it. Then a Modbus register that never answered, then the
    tracker's own account of its samples, then whether the counters have been
    read at all, and last the counters' pair itself.
    """
    if display is not None and result.basis != "first_year":
        return display
    if consumed is None and not modbus_answered:
        return "registret 62341 har inte svarat, så tillförd energi saknas"
    if result.value is not None:
        return None
    if result.reason is not None:
        return result.reason
    if out is None or consumed is None:
        if result.basis == "first_year":
            return "första året är inte fullt ännu"
        return "räknarna har inte lästs"
    return cop_reason(
        None, result.basis, result.energy_out, result.energy_in, hours, modbus_answered,
        days=result.days,
    )


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
        """Store the counters: one per day for the year, and a timestamped run.

        ``now`` is the moment the counters were read off the panel, where the
        caller knows it. A reading is recorded once: a moment no newer than
        the newest sample already kept is the same reading coming round again,
        as it does when the page the counters sit on has not been reached
        since, or when the pair comes back out of the display store after a
        restart, and recording it again would stamp a day's sample with a
        pair that was read on another day.
        """
        if energy_out is None or energy_in is None:
            return
        await self.async_load()
        if now is not None:
            newest = self.last_sample_at
            if newest is not None and now <= newest:
                return
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

    @property
    def last_sample_at(self) -> datetime | None:
        """When the newest timestamped sample was read, or None without one."""
        stamps = []
        for row in self._recent:
            try:
                stamps.append(_parse(row[0]))
            except (TypeError, ValueError):
                continue
        return max(stamps, default=None)

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
        """The machine's first year, or nothing until it has had one.

        Without the commissioning day there will never be one: it is worked
        out from the display's powered-on hours, and a page without that row,
        as the i550 Pro's history page is, leaves the start unknown. Said so,
        rather than promising a year that is not coming.
        """
        if self._first_year is None:
            reason = "driftstarten är okänd" if self._anchor is None else None
            return CopResult(None, "first_year", 0, reason=reason)
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
            return CopResult(
                None, "day", 0, reason="väntar på ett prov som är 20 till 30 timmar gammalt"
            )
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

    def _window(
        self,
        energy_out: float,
        energy_in: float,
        basis: str,
        days: int,
        tolerance: int,
        floor: float,
        now: date,
        per_day: bool = False,
    ) -> CopResult:
        """The figure across a window, from the newest sample at least ``days`` old.

        The base sample may lie up to ``tolerance`` days further back, so a gap
        of a day or two in the samples does not cost the figure; beyond that
        the span would no longer be what the name says. Without a sample in
        that band the result says how far the samples have come, or that a gap
        in them covers the band, which is the tracker's own knowledge. What
        the counters themselves have to say is left to :func:`cop_reason`.

        With ``per_day`` the floor is the daily one times the days the figure
        actually spans: a week's deltas are a week of whole kilowatt hours, and
        the rounding they carry is the same as a day's, so what holds for three
        over a day holds for twenty-one over seven.
        """
        window_start = (now - timedelta(days=days)).isoformat()
        earliest = (now - timedelta(days=days + tolerance)).isoformat()
        older = sorted(d for d in self._samples if earliest <= d <= window_start)
        if older:
            base_out, base_in = self._samples[older[-1]]
            span = (now - date.fromisoformat(older[-1])).days
            delta_out = energy_out - base_out
            delta_in = energy_in - base_in
            if delta_out < 0 or delta_in < 0:
                # A counter that went backwards means the unit was replaced or
                # reset. No quotient across that is honest.
                return CopResult(
                    None, basis, span,
                    reason=f"räknarna har gått bakåt sedan {older[-1]}, enheten är bytt eller nollställd",
                )
            if per_day:
                floor = floor * max(1, span)
            return CopResult(
                _ratio(delta_out, delta_in, floor), basis, span,
                round(delta_out, 1), round(delta_in, 1),
            )
        if not self._samples:
            return CopResult(None, basis, 0, reason=f"0 av {days} dygn samlade, inget prov sparat ännu")
        oldest = min(self._samples)
        collected = (now - date.fromisoformat(oldest)).days
        if collected > days + tolerance:
            return CopResult(
                None, basis, days,
                reason=f"inget sparat prov är {days} till {days + tolerance} dygn gammalt",
            )
        collected = max(0, min(collected, days))
        return CopResult(
            None, basis, collected,
            reason=f"{collected} av {days} dygn samlade, äldsta provet {oldest}",
        )

    def result_year(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The rolling year, or how far the samples have come towards one.

        Only the tracker's own samples count. The commissioning day is not
        one of them here: it made the first anniversary look like a rolling
        year for the fifteen days the anchor lay inside the band, after which
        the figure vanished until the samples had reached a year of their own.
        The first year carries that figure instead, for good.
        """
        if energy_out is None or energy_in is None:
            return CopResult(None, "year", 0)
        now = today or date.today()
        return self._window(
            energy_out, energy_in, "year",
            COP_WINDOW_DAYS, YEAR_MAX_DAYS - COP_WINDOW_DAYS, MIN_CONSUMPTION_KWH, now,
        )

    def result_window(
        self,
        energy_out: float | None,
        energy_in: float | None,
        basis: str,
        today: date | None = None,
    ) -> CopResult:
        """The figure over one of the shorter windows in :data:`WINDOWS`.

        Between the day and the year there was nothing: a lifetime figure says
        nothing about this autumn, and the display's own "/30 dagar" rows
        stand at zero on both units they have been read from. The daily
        samples the tracker keeps for the year carry a week and a month from
        the day they are old enough, which on a fresh installation is a week
        and a month after it was set up.
        """
        days, tolerance = WINDOWS[basis]
        if energy_out is None or energy_in is None:
            return CopResult(None, basis, 0)
        now = today or date.today()
        return self._window(
            energy_out, energy_in, basis, days, tolerance, MIN_CONSUMPTION_KWH_DAY, now,
            per_day=True,
        )

    def result_lifetime(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The figure over everything the counters have counted.

        The one span that needs no waiting, and the one that must never be
        reported as anything else. The days it covers are counted from the
        commissioning day where that is known, since the counters started from
        zero then, and otherwise from the oldest sample the tracker holds.
        """
        if energy_out is None or energy_in is None:
            return CopResult(None, "lifetime", 0)
        now = today or date.today()
        since = self._anchor or (min(self._samples) if self._samples else None)
        span = max(0, (now - date.fromisoformat(since)).days) if since else 0
        return CopResult(
            _ratio(energy_out, energy_in),
            "lifetime",
            span,
            round(energy_out, 1),
            round(energy_in, 1),
        )

    def result(
        self,
        energy_out: float | None,
        energy_in: float | None,
        today: date | None = None,
    ) -> CopResult:
        """The rolling year where there is one, otherwise the lifetime figure.

        For callers that want one number whatever its span, with the span
        stated in ``basis``. The sensors ask for each span by name instead.
        """
        year = self.result_year(energy_out, energy_in, today)
        if year.value is not None:
            return year
        return self.result_lifetime(energy_out, energy_in, today)


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

    def seed(self, read_at: Any, value: float | None) -> None:
        """Start from a pair written down before a restart.

        The display value comes back from its store with the moment it was
        read, and the Modbus reading that was taken at that moment comes back
        here, so the pair is the same pair; a fresh Modbus reading paired with
        the old display value would be off by however long Home Assistant
        was away.
        """
        if read_at is None:
            return
        self._read_at = read_at
        self.value = value

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


def counter_rows(runtime: Any) -> list[Any]:
    """The display rows the two lifetime counters are read off.

    Delivered heat always; consumed energy only where the display has the
    row, since on the Modbus route the consumption is a register taken at
    the moment delivered heat was read, and shares that row's age.
    """
    rows = [getattr(runtime, "energy_out", None), getattr(runtime, "energy_in", None)]
    return [row for row in rows if row is not None]


def counters_read_at(runtime: Any) -> datetime | None:
    """When the pair :func:`current_totals` returns was read off the panel.

    None unless every counter row has been read and is still fresh by the
    coordinator's rule (R5), so a page that has stopped being reached gives
    no sample at all rather than yesterday's pair stamped with today. The
    moment is the delivered heat row's, which is also the moment the Modbus
    consumption was paired with it; where both counters sit on the display
    they sit on the same page and share it.
    """
    web = getattr(runtime, "web", None)
    rows = counter_rows(runtime)
    if web is None or not rows:
        return None
    for row in rows:
        if row_read_at(web, row.key) is None or row_is_fresh(web, row.key) is False:
            return None
    return row_read_at(web, rows[0].key)


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
    figures["cop_year"] = tracker.result_year(out, consumed).value
    figures["cop_day"] = tracker.result_day(out, consumed).value
    figures["cop_first_year"] = tracker.result_first_year().value
    if out is not None and consumed is not None and consumed >= MIN_CONSUMPTION_KWH:
        figures["cop_lifetime"] = round(out / consumed, 2)
    return figures
