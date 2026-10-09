"""One Modbus poll round, kept free of Home Assistant so it can be proven.

A round reads the planned register blocks one after another on the single
connection. Two things can go wrong with a block and they are treated in
opposite ways, as modbus_api explains: silence, the controller keeping quiet
because the model lacks the registers, costs that block and nothing else; a
transport failure, the connection being gone, ends the round at once, since
every block after it would only wait out the same timeout against a dead line.

The round also keeps two small books for the coordinator. Which blocks the
model evidently lacks, so they stop costing a timeout every round. And whether
rounds are taking longer than the interval between them, said once per episode
rather than once per round.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterable

from .modbus_api import CtcModbusError, CtcModbusSilence, CtcModbusTransportError

_LOGGER = logging.getLogger(__name__)

#: Rounds in a row a block may go unanswered, while the rest answer, before it
#: is taken to be missing on this model.
MISSING_PATIENCE = 3


@dataclass
class RoundResult:
    """What one round brought back and what it cost."""

    #: Register values by address.
    raw: dict[int, int] = field(default_factory=dict)
    #: Starts of the blocks that answered.
    answered: list[int] = field(default_factory=list)
    #: Starts of the blocks that did not, with the line still up afterwards.
    unanswered: list[int] = field(default_factory=list)
    #: Starts of the blocks whose silence took the line with it. Kept apart
    #: because the next block pays for a new connection, but held against the
    #: block like any other silence: pymodbus before 3.8 closes the line after
    #: every silent request, so on that library a block the model lacks would
    #: otherwise never be learnt and would cost a timeout, a settle and a new
    #: connection every round for ever.
    dropped: list[int] = field(default_factory=list)
    #: Seconds the round took.
    elapsed: float = 0.0


def lead_with(blocks: Iterable[tuple[int, int]], address: int) -> list[tuple[int, int]]:
    """The same blocks with the one holding ``address`` first, the rest in their order.

    The plan is sorted by address, which puts the stored block that some models
    lack ahead of the register every model answers. Asked for first, that
    register tells a controller that answers nothing from a model that lacks a
    block after one timeout instead of twelve, and the blocks that follow are
    still given their chance and learnt in the usual way.
    """
    ordered = list(blocks)
    for index, (start, count) in enumerate(ordered):
        if start <= address < start + count:
            return [ordered[index], *ordered[:index], *ordered[index + 1:]]
    return ordered


async def read_round(
    client: Any,
    blocks: Iterable[tuple[int, int]],
    skip: Iterable[int] = (),
    probe: int | None = None,
) -> RoundResult:
    """Read every planned block except those in ``skip``.

    Raises CtcModbusTransportError as soon as the connection is found gone, and
    also when not a single block answered in the whole round: whatever the
    socket believes then, the line is dead to us, so the client is let go and
    the next round starts over after the settle time.

    ``probe`` names the one register every model answers. When the block that
    holds it is silent while nothing has answered yet in this round, the line
    is given up there and then rather than after a timeout for every block
    left: a controller whose TCP side is up while its Modbus side says nothing
    would otherwise cost two to four intervals before the entities went
    unavailable. A silence from the probe block after another block answered
    is only that block's, like any other.
    """
    started = time.monotonic()
    result = RoundResult()
    left_out = set(skip)
    first_tried: int | None = None
    for start, count in blocks:
        if start in left_out:
            continue
        if first_tried is None:
            first_tried = start
        try:
            values = await client.async_read(start, count)
        except CtcModbusTransportError as err:
            # A failed connect does not know which register it was for; the
            # round does, and the coordinator's message should say.
            if err.address is None:
                err.address = start
            raise
        except CtcModbusSilence as err:
            _LOGGER.debug("%s", err)
            if probe is not None and not result.answered and start <= probe < start + count:
                await client.async_close()
                raise CtcModbusTransportError(
                    f"register {probe}, which every model answers, did not answer and "
                    "nothing else has in this round, so the connection is given up and "
                    "the next round starts over",
                    start,
                ) from err
            (result.unanswered if err.line_up else result.dropped).append(start)
            continue
        except CtcModbusError as err:
            # The controller answered, but with an exception code rather than
            # data. For the books that is the same as no answer.
            _LOGGER.debug("%s", err)
            result.unanswered.append(start)
            continue
        result.answered.append(start)
        for offset, value in enumerate(values):
            result.raw[start + offset] = value
    result.elapsed = time.monotonic() - started
    if first_tried is not None and not result.answered:
        await client.async_close()
        raise CtcModbusTransportError(
            "no register answered in a whole round, so the connection is given up "
            "and the next round starts over",
            first_tried,
        )
    return result


class MissingBlocks:
    """Blocks this model evidently lacks, learnt from rounds of silence.

    A block joins the set after MISSING_PATIENCE rounds in a row without an
    answer while other blocks did answer: silence from one block with the rest
    talking is the register, silence from all of them is the line. Whether the
    library closed the connection after the silence makes no difference to the
    block, since older pymodbus does that after every silence; the rest having
    answered in the same round is what tells the register from the line. A
    block that answers again is forgiven on the spot. Nothing is stored, so a
    restart gives every block a fresh chance.
    """

    def __init__(self, patience: int = MISSING_PATIENCE) -> None:
        self.patience = patience
        self.missing: set[int] = set()
        self._streak: dict[int, int] = {}

    def note(self, result: RoundResult) -> list[int]:
        """Record a round; returns the blocks that were just given up on."""
        if not result.answered:
            return []
        for start in result.answered:
            self._streak.pop(start, None)
        learnt: list[int] = []
        for start in (*result.unanswered, *result.dropped):
            self._streak[start] = self._streak.get(start, 0) + 1
            if self._streak[start] >= self.patience and start not in self.missing:
                self.missing.add(start)
                learnt.append(start)
        return learnt


class SlowRounds:
    """Say once when rounds start taking longer than the interval, and once when they stop."""

    def __init__(self) -> None:
        self.slow = False

    def note(self, elapsed: float, interval: float) -> str | None:
        """Returns "slow" on entering an episode, "recovered" on leaving one."""
        if interval <= 0:
            return None
        if elapsed > interval and not self.slow:
            self.slow = True
            return "slow"
        if elapsed <= interval and self.slow:
            self.slow = False
            return "recovered"
        return None
