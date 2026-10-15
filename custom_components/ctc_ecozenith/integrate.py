"""Energy of the integration's own making, where the machine's counters stay at zero.

Four installations in the fleet answer a clean zero on both lifetime counters.
The rows are there on the history page and they hand over numbers; nothing ever
fills them, and no coefficient of performance can be worked out from two zeroes.
**It follows the outdoor unit, not the indoor one:** the one installation that
fills them has a 700 series outdoor unit, and the ones that do not are a 400
series and two in the 600 series, behind two different indoor models. Four data
points, so it is where the evidence points, not something CTC documents.

The heat pump's own page carries the two powers instead, delivered heat and
supplied power in kilowatts, and energy is what power does over time, so the
integration adds them up itself. Whether those rows carry anything on a 400
or 600 series is the open question, and the report answers it within a day:
``power_rows`` says the pair was found, ``cop_integrated`` says the sums came to
something.

What makes the quotient worth having is that both powers are read in the same
harvest, at the same instant. A compressor that cycles makes a single sample too
high or too low, but it makes BOTH too high or too low by nearly the same factor,
so the error largely cancels in a ratio while it does not cancel in either total.
The totals are therefore rough on purpose and say so where they are shown; the
figure worked out from them is the point.

Nothing is invented across a gap: a sample that arrives after too long a silence
starts the sum again from itself rather than filling in hours nobody measured.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: Longer than this between two harvests and what the pump did in between is
#: unknown. The display is harvested every half hour by default, and a cycle that
#: is skipped keeps its previous values, so two hours leaves room for a few missed
#: turns without ever stretching one sample across a night.
MAX_GAP = timedelta(hours=2)

#: The sums are written down with a delay, so a harvest every half hour does not
#: mean a disk write every half hour.
SAVE_DELAY = 600


def _parse(stamp: str) -> datetime:
    value = datetime.fromisoformat(stamp)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


class PowerIntegrator:
    """Two running sums of kilowatt hours, from two kilowatt readings over time."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._out = 0.0
        self._in = 0.0
        #: How much time the sums actually rest on, gaps excluded.
        self._hours = 0.0
        self._last: tuple[datetime, float, float] | None = None
        self._loaded = False

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load()
        if not isinstance(data, dict):
            return
        self._out = _number(data.get("out")) or 0.0
        self._in = _number(data.get("in")) or 0.0
        self._hours = _number(data.get("hours")) or 0.0
        last = data.get("last")
        if isinstance(last, (list, tuple)) and len(last) == 3:
            try:
                self._last = (_parse(str(last[0])), float(last[1]), float(last[2]))
            except (TypeError, ValueError):
                self._last = None

    @property
    def totals(self) -> tuple[float | None, float | None]:
        """The two sums, or nothing at all until they rest on measured time."""
        if self._hours <= 0:
            return None, None
        return round(self._out, 1), round(self._in, 1)

    @property
    def hours(self) -> float:
        return round(self._hours, 1)

    def note(self, when: Any, out_kw: Any, in_kw: Any) -> bool:
        """Take one pair of readings. True when it added to the sums.

        The pair has to come from the same read, which is what the caller passes:
        both rows sit on the same page, so one timestamp covers them.
        """
        out = _number(out_kw)
        consumed = _number(in_kw)
        if not isinstance(when, datetime) or out is None or consumed is None:
            return False
        previous = self._last
        self._last = (when, out, consumed)
        if previous is None:
            return False
        hours = (when - previous[0]).total_seconds() / 3600
        if hours <= 0 or hours > MAX_GAP.total_seconds() / 3600:
            # The same harvest twice, a clock that went backwards, or a silence
            # too long to account for. Begin again from this sample.
            if hours > 0:
                _LOGGER.debug("%.1f h since the last power reading, too long to add", hours)
            return False
        self._out += (previous[1] + out) / 2 * hours
        self._in += (previous[2] + consumed) / 2 * hours
        self._hours += hours
        return True

    def as_dict(self) -> dict[str, Any]:
        last = self._last
        return {
            "out": round(self._out, 3),
            "in": round(self._in, 3),
            "hours": round(self._hours, 4),
            "last": [last[0].isoformat(), last[1], last[2]] if last else None,
        }

    def save_soon(self) -> None:
        """Write the sums down, batched: the store delays and coalesces."""
        delay = getattr(self._store, "async_delay_save", None)
        if delay is not None:
            delay(self.as_dict, SAVE_DELAY)

    async def async_save(self) -> None:
        await self._store.async_save(self.as_dict())
