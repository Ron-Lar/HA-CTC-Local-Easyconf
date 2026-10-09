"""The age of a display reading, and what the last harvest left behind.

The display is harvested every half hour, and a value is carried from one
harvest to the next whenever its page was not reached: the data keeps it, so
that a page the panel could not be walked to for a moment does not empty its
sensors. Carried too long, though, a value is a lie with a timestamp from the
future, and this module holds the rule that ends that: a reading is fresh for
as many intervals as a whole harvest may fail in a row, and after that its
sensor is called unavailable until the page is read again.

Free of Home Assistant, like patience.py and seen.py, so the rule can be
tested on its own and the store is handed in.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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
