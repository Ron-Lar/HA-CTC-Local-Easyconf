"""The age of a display reading, and what the last harvest left behind.

The display is harvested every half hour, and a value is carried from one
harvest to the next whenever its page was not reached: the data keeps it, so
that a page the panel could not be walked to for a moment does not empty its
sensors. Carried too long, though, a value is a lie with a timestamp from the
future, and this module holds the rule that ends that: a reading is fresh for
as many intervals as a whole harvest may fail in a row, and after that its
sensor is called unavailable until the page is read again.

The last harvest is also written down, values and moments alike, so that a
restart of Home Assistant costs the panel nothing: the sensors come up with
what was read before the restart, as old as it is, and the first harvest is
made when it would have been made anyway, one interval after the last one.
Only an installation with nothing written down harvests at once.

Free of Home Assistant, like patience.py and seen.py, so the rules can be
tested on their own and the store is handed in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

_LOGGER = logging.getLogger(__name__)

#: The Store's own format version. The key under which it is kept is built
#: in __init__.py, beside the seen and cop stores of the same entry.
STORAGE_VERSION = 1

#: How long a harvest may wait before it is written down. Short: the point
#: of the store is the moment the harvest happened, and a restart soon after
#: a harvest is exactly when it is needed.
SAVE_DELAY_SECONDS = 10

#: The least a first harvest waits after set-up, even when it is overdue, so
#: that the platforms are in place and listening before the panel moves.
MIN_FIRST_DELAY = 5.0


def stale_after(interval: float, patience: int) -> timedelta:
    """How long a reading stays fresh: as long as a harvest may fail in a row.

    The same number of intervals after which a display that answers nothing
    at all is called unavailable, so a page that has stopped answering is
    treated the way the whole display would be, no sooner and no later.
    """
    return timedelta(seconds=max(0, patience) * max(0.0, float(interval)))


def is_fresh(read_at: datetime | None, now: datetime, limit: timedelta) -> bool:
    """Whether a reading taken at ``read_at`` is still worth showing at ``now``."""
    if read_at is None:
        return False
    return now - read_at <= limit


def utcnow() -> datetime:
    """The moment, timezone aware, as the readings are stamped."""
    return datetime.now(timezone.utc)


def parse_moment(text: Any) -> datetime | None:
    """A stored moment back into a datetime, UTC where the text names no zone."""
    if not isinstance(text, str) or not text:
        return None
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass
class StoredHarvest:
    """What the last harvest left: the values, when each was read, and when it ran."""

    values: dict[str, float] = field(default_factory=dict)
    read_at: dict[str, datetime] = field(default_factory=dict)
    harvested_at: datetime | None = None
    #: Modbus 62341 as it stood when the display's heat counter was read, on
    #: the installations whose display has no consumption counter of its own.
    #: Kept beside the display values so the pair survives a restart as a
    #: pair; see cop.ConsumptionSnapshot.
    consumption: float | None = None

    @property
    def latest(self) -> datetime | None:
        """The most recent moment anything was read, or when the harvest ran."""
        moments = [m for m in self.read_at.values() if m is not None]
        if self.harvested_at is not None:
            moments.append(self.harvested_at)
        return max(moments) if moments else None

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": {key: value for key, value in self.values.items()},
            "read_at": {key: moment.isoformat() for key, moment in self.read_at.items()},
            "harvested_at": self.harvested_at.isoformat() if self.harvested_at else None,
            "consumption": self.consumption,
        }

    @classmethod
    def from_dict(cls, stored: Any) -> "StoredHarvest | None":
        """Rebuild what was written, or None when nothing usable was.

        A value without a number, or a moment that does not parse, is left
        out rather than taken on trust: the store is seed, not truth, and
        the next harvest overwrites it.
        """
        if not isinstance(stored, Mapping):
            return None
        values: dict[str, float] = {}
        for key, value in (stored.get("values") or {}).items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            values[str(key)] = float(value)
        read_at: dict[str, datetime] = {}
        for key, text in (stored.get("read_at") or {}).items():
            moment = parse_moment(text)
            if moment is not None and str(key) in values:
                read_at[str(key)] = moment
        # A value whose moment is unknown cannot be judged fresh, so it is of
        # no use to the sensors and is left out.
        values = {key: value for key, value in values.items() if key in read_at}
        consumption = stored.get("consumption")
        if isinstance(consumption, bool) or not isinstance(consumption, (int, float)):
            consumption = None
        harvest = cls(
            values=values,
            read_at=read_at,
            harvested_at=parse_moment(stored.get("harvested_at")),
            consumption=None if consumption is None else float(consumption),
        )
        return harvest if harvest.latest is not None else None


def first_harvest_delay(
    stored: StoredHarvest | None, interval: float, now: datetime
) -> float:
    """Seconds until the first harvest after a start is owed.

    One interval after the last reading, as if Home Assistant had never been
    away, so a restart within the interval moves the panel not at all and a
    longer absence harvests as soon as the platforms are up. With nothing
    stored there is nothing to show, and the harvest is made at once.
    """
    if stored is None or stored.latest is None:
        return MIN_FIRST_DELAY
    due = stored.latest + timedelta(seconds=float(interval))
    return max(MIN_FIRST_DELAY, (due - now).total_seconds())


class HarvestMemory:
    """The last harvest, kept in a Store handed in by the caller."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._remembered_at: datetime | None = None
        #: The last harvest handed to the store with its delay, until the
        #: store has written it; what :meth:`async_flush` writes out early.
        self._pending: dict[str, Any] | None = None

    async def async_load(self) -> StoredHarvest | None:
        try:
            stored = StoredHarvest.from_dict(await self._store.async_load())
        except Exception as err:  # noqa: BLE001 - a store that cannot be read is an empty one
            _LOGGER.debug("Could not read the stored harvest: %s", err)
            return None
        if stored is not None:
            self._remembered_at = stored.harvested_at
        return stored

    def remember(
        self,
        values: Mapping[str, Any] | None,
        read_at: Mapping[str, datetime],
        harvested_at: datetime | None,
        consumption: float | None = None,
    ) -> bool:
        """Write the harvest down, once per harvest. True when a save was scheduled.

        Called from the coordinator's listeners, which run after every
        refresh, a skipped or failed one included; only a harvest that
        actually ran since the last save is written.
        """
        if harvested_at is None or harvested_at == self._remembered_at:
            return False
        harvest = StoredHarvest(
            values={
                key: float(value)
                for key, value in (values or {}).items()
                if isinstance(value, (int, float)) and not isinstance(value, bool)
            },
            read_at={key: moment for key, moment in read_at.items() if key in (values or {})},
            harvested_at=harvested_at,
            consumption=consumption,
        )
        payload = harvest.as_dict()

        def _payload() -> dict[str, Any]:
            # Called by the store when it writes: nothing is waiting after that.
            if self._pending is payload:
                self._pending = None
            return payload

        self._pending = payload
        try:
            self._store.async_delay_save(_payload, SAVE_DELAY_SECONDS)
        except Exception as err:  # noqa: BLE001 - a lost save only costs one walk at the next start
            _LOGGER.debug("Could not schedule saving the harvest: %s", err)
            self._pending = None
            return False
        self._remembered_at = harvested_at
        return True

    async def async_flush(self) -> bool:
        """Write a harvest that is still waiting out its delay down now. True when one was.

        The delay is for the common case, where nothing hurries. A reload
        does: the entry it brings reads the store at set-up, and a harvest
        that ran a moment before the reload would otherwise be ten seconds
        short of being there, which costs the panel a whole walk more. The
        store cancels the delayed write when it is given the data outright.
        """
        payload = self._pending
        if payload is None:
            return False
        self._pending = None
        try:
            await self._store.async_save(payload)
        except Exception as err:  # noqa: BLE001 - the delayed write is lost with it, nothing else
            _LOGGER.debug("Could not write the harvest down: %s", err)
            return False
        return True
