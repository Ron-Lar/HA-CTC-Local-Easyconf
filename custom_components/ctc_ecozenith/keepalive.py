"""What Home Assistant may truthfully say is in force in the controller.

CTC's 1000 block is volatile: the controller holds a value for about five
minutes after the last write that reached it and then forgets it. So an
override is only in force from a write that succeeded, and only for as long as
one has succeeded recently. Three failed refreshes in a row are three minutes,
which the unit still covers; two more and the honest state is that the
override is gone, whatever Home Assistant would like it to be. Time decides,
not a count of misses, because the controller counts time.

Free of Home Assistant, like patience.py, so the rule can be tested on its own.
The clock is handed in as seconds and never read here.
"""

from __future__ import annotations


class Keepalive:
    """The overrides in force, each with the moment it last reached the unit."""

    def __init__(self, expiry: float) -> None:
        self._expiry = expiry
        self._values: dict[int, int] = {}
        self._ok_at: dict[int, float] = {}
        self._failures: dict[int, int] = {}

    @property
    def active(self) -> dict[int, int]:
        """The raw value in force per address, a copy."""
        return dict(self._values)

    def __bool__(self) -> bool:
        return bool(self._values)

    def get(self, address: int) -> int | None:
        return self._values.get(address)

    def written_at(self, address: int) -> float | None:
        """When a write of this address last reached the controller."""
        return self._ok_at.get(address)

    def valid_until(self, address: int) -> float | None:
        """Until when the controller is known to hold this address."""
        ok_at = self._ok_at.get(address)
        return None if ok_at is None else ok_at + self._expiry

    def failures(self, address: int) -> int:
        """How many writes in a row have failed since the last one that reached it."""
        return self._failures.get(address, 0)

    def written(self, address: int, raw: int, now: float) -> bool:
        """Record a write that reached the controller.

        True when it ends a run of failures, which is worth a word in the log.
        """
        recovered = self._failures.pop(address, 0) > 0
        self._values[address] = raw
        self._ok_at[address] = now
        return recovered

    def failed(self, address: int, now: float) -> bool:
        """Record a write of an active address that did not reach the controller.

        True when the address has now been given up: the last write that reached
        the unit is older than the expiry, so the unit has forgotten the value
        and it is dropped here as well. An address that is not in force is not
        counted, since nothing was claimed about it.
        """
        if address not in self._values:
            return False
        self._failures[address] = self._failures.get(address, 0) + 1
        ok_at = self._ok_at.get(address)
        if ok_at is not None and now - ok_at < self._expiry:
            return False
        self.release(address)
        return True

    def release(self, address: int) -> bool:
        """Forget one address. True when it was in force."""
        was = address in self._values
        self._values.pop(address, None)
        self._ok_at.pop(address, None)
        self._failures.pop(address, None)
        return was

    def clear(self) -> None:
        self._values.clear()
        self._ok_at.clear()
        self._failures.clear()
