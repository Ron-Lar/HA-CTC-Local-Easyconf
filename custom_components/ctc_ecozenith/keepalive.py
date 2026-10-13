"""What Home Assistant may truthfully say is in force in the controller.

CTC's 1000 block is volatile: the controller holds a value for about five
minutes after the last write that reached it and then forgets it. So an
override is only in force from a write that succeeded, and only for as long as
one has succeeded recently. Three failed refreshes in a row are three minutes,
which the unit still covers; two more and the honest state is that the
override is gone, whatever Home Assistant would like it to be. Time decides,
not a count of misses, because the controller counts time.

A write takes time, and the state can change while it is on its way: a release
or a newer value. Each address therefore carries a generation that every
change moves on, so whoever waited on a write can tell whether what it is
about to record is still the state it set out from. A release is also counted
on its own, per address and whether or not the address was held, and letting
go of everything moves an epoch: a set reads both before it even waits for its
turn to write, so a release that comes while it waits, or while its write is
the one that would have put the address in force, is not overruled by it.
Giving up an address the unit has forgotten is no release in that sense, since
nobody let go of it, and a newer value on its way is still wanted.

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
        self._generation: dict[int, int] = {}
        self._releases: dict[int, int] = {}
        self._epoch = 0

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

    def generation(self, address: int) -> int:
        """A number that moves on whenever this address is written or stops being in force.

        Read it before a write goes out and again when the write is done: if
        it changed in between, somebody released or re-set the address while
        the write was on its way, and the write's outcome is theirs to
        overrule, not the other way round.
        """
        return self._generation.get(address, 0)

    def releases(self, address: int) -> int:
        """How many times this address has been let go of, whether it was held or not.

        Unlike the generation, a release of an address that is not in force
        counts too: the write that would put it in force may be on its way.
        """
        return self._releases.get(address, 0)

    @property
    def epoch(self) -> int:
        """A number that moves on whenever everything is let go of at once."""
        return self._epoch

    def _moved(self, address: int) -> None:
        self._generation[address] = self._generation.get(address, 0) + 1

    def written(self, address: int, raw: int, now: float) -> bool:
        """Record a write that reached the controller.

        True when it ends a run of failures, which is worth a word in the log.
        """
        recovered = self._failures.pop(address, 0) > 0
        self._values[address] = raw
        self._ok_at[address] = now
        self._moved(address)
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
        return self.expire(address, now)

    def expire(self, address: int, now: float) -> bool:
        """Give the address up if the unit has forgotten it by now. True when it was.

        What failed() does after counting a miss, for a check made at the
        moment the controller forgets, without a write having to fail first.
        Not counted as a release: nobody let go of the address.
        """
        ok_at = self._ok_at.get(address)
        if address not in self._values or ok_at is None or now - ok_at < self._expiry:
            return False
        self._forget(address)
        return True

    def release(self, address: int) -> bool:
        """Let go of one address. True when it was in force.

        Counted in releases() either way, so a write of the address that is
        on its way, held or not, can tell that it was let go of meanwhile.
        """
        self._releases[address] = self._releases.get(address, 0) + 1
        return self._forget(address)

    def _forget(self, address: int) -> bool:
        was = address in self._values
        self._values.pop(address, None)
        self._ok_at.pop(address, None)
        self._failures.pop(address, None)
        if was:
            self._moved(address)
        return was

    def clear(self) -> None:
        """Let go of everything, and move the epoch for the writes on their way."""
        self._epoch += 1
        for address in list(self._values):
            self._moved(address)
        self._values.clear()
        self._ok_at.clear()
        self._failures.clear()
