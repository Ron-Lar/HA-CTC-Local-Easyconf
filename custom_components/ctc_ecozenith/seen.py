"""Which readings this installation has ever given a value other than zero.

CTC's controller answers for hardware that is not fitted, and for the registers a
model or software revision does not use, with a clean zero: a brine pump on an air
to water heat pump, current sensors that were never installed, the refrigerant
circuit of a unit whose compressor has never run. What a given installation has is
therefore something only that installation can tell, by giving a value. Every value
the two coordinators read is noted here once it is a number other than zero, and the
CTC page leaves out a reading that is zero and has never been anything else.

A second set remembers which keys have ever been a number at all, a true zero
included. The display answers for a sensor that is not fitted with a marker,
9999, which is no number, and a row that has only ever read the marker gets no
entity until it first leaves one (sensor.py, roadmap L3). The zero set cannot
serve for that: a row of true zeros is a number every time.

Kept in a Store per entry, so a value seen once stays known across restarts. Free of
Home Assistant imports: the store is handed in, as for cop.CopTracker.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Callable, Iterable, Mapping

_LOGGER = logging.getLogger(__name__)

#: How long a change to the set may wait before it is written down.
SAVE_DELAY_SECONDS = 60

#: The store's format. The major version stays at 1 for as long as an older
#: release can still make sense of the file: Home Assistant refuses a file whose
#: major version is above the one the code opens it with, so raising it would
#: leave a return to that release with an entry that cannot be set up at all.
#: What changes within the major is told by the minor version, which older code
#: reads straight through, keeping what it does not know and writing it back.
STORAGE_VERSION = 1

#: Minor 1 held the zero set alone, as ``{"keys": [...]}``. Minor 2 adds
#: ``numeric``, the keys that have ever been a number; see :func:`migrate`.
STORAGE_MINOR_VERSION = 2

#: How a display row's key begins: the page it is harvested from.
_DISPLAY_KEY = re.compile(r"^p\d+_")


def is_zero(value: Any) -> bool:
    """A number that is exactly zero. Anything else, text included, is a value."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value == 0
    if isinstance(value, str):
        try:
            return float(value) == 0
        except ValueError:
            return False
    return False


def is_number(value: Any) -> bool:
    """A number, zero included. Text is not one, nor is a boolean."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_display_key(key: str) -> bool:
    """Whether a key names a display row, ``p22_utetemperatur``."""
    return _DISPLAY_KEY.match(key) is not None


def migrate(data: Any) -> dict[str, list[str]]:
    """The store's content as minor 2 writes it, from whatever shape is on disk.

    The shape decides, not a version number: a file an older release wrote
    back after a return still says minor 1, yet may carry the numeric set the
    newer release had put there, and nothing in it is worth throwing away.
    Minor 1 knew only which keys had been something other than zero. A display
    row among them was a number, since the display's rows never read as
    anything else, so those seed the numeric set, beside whatever numeric set
    the file already holds; a Modbus key may have been an enumeration's label,
    and is not presumed. Rubbish gives an empty store.
    """
    keys: set[str] = set()
    numeric: set[str] = set()
    if isinstance(data, Mapping):
        if isinstance(data.get("keys"), list):
            keys = {str(key) for key in data["keys"]}
        if isinstance(data.get("numeric"), list):
            numeric = {str(key) for key in data["numeric"]}
        numeric |= {key for key in keys if is_display_key(key)}
    return {"keys": sorted(keys), "numeric": sorted(numeric)}


class SeenValues:
    """The keys that have had a value other than zero, ever, and those that were numbers."""

    def __init__(self, store: Any, on_new: Callable[[], None] | None = None) -> None:
        self._store = store
        self._on_new = on_new
        self.keys: set[str] = set()
        #: Keys that have ever read as a number, a true zero included.
        self.numeric: set[str] = set()
        #: True until something was stored: an installation new to this, whose
        #: past values are worth looking up in the recorded history once.
        self.fresh = True

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if isinstance(stored, Mapping) and isinstance(stored.get("keys"), list):
            # The store migrates on disk; this reads the old shape as well,
            # for a store handed in by something that does not.
            data = migrate(stored)
            self.keys = set(data["keys"])
            self.numeric = set(data["numeric"])
            self.fresh = False

    def note(self, data: Mapping[str, Any] | None) -> None:
        """Take in a coordinator's data, and remember what is new."""
        items = list((data or {}).items())
        self._remember(
            (key for key, value in items if value is not None and not is_zero(value)),
            (key for key, value in items if is_number(value)),
        )

    def add(self, keys: Iterable[Any]) -> int:
        """Remember these keys as having had a value. Returns how many were new.

        A display row with a value was a number, the display's rows never read
        as anything else, so it joins the numeric set as well; that is what
        lets the record seeded from the recorder's statistics count there too.
        """
        return self._remember(keys, ())[0]

    def add_numeric(self, keys: Iterable[Any]) -> int:
        """Remember these keys as having read as a number. Returns how many were new."""
        return self._remember((), keys)[1]

    def _remember(self, valued: Iterable[Any], numbers: Iterable[Any]) -> tuple[int, int]:
        """Take in both kinds at once: one save, and one announcement.

        Only a new key with a value is announced: the page's choice of what to
        show rests on that set, while the numeric one decides only which
        display rows get an entity.
        """
        new_keys = {str(key) for key in valued} - self.keys
        new_numeric = ({str(key) for key in numbers} | {k for k in new_keys if is_display_key(k)}) - self.numeric
        if not new_keys and not new_numeric:
            return 0, 0
        self.keys |= new_keys
        self.numeric |= new_numeric
        self._save()
        if new_keys and self._on_new is not None:
            self._on_new()
        return len(new_keys), len(new_numeric)

    def _save(self) -> None:
        try:
            self._store.async_delay_save(
                lambda: {"keys": sorted(self.keys), "numeric": sorted(self.numeric)},
                SAVE_DELAY_SECONDS,
            )
        except Exception as err:  # noqa: BLE001 - a lost save only means a later rediscovery
            _LOGGER.debug("Could not schedule saving the seen values: %s", err)

    def unused(self, values: Mapping[str, Any]) -> set[str]:
        """Of these current values, the keys that are zero and never were anything else."""
        return {key for key, value in values.items() if key not in self.keys and is_zero(value)}
