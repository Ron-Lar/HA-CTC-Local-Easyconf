"""The harvest gives way to somebody at the panel, but not for good (R8).

A round that finds the panel somewhere other than where the last round left it
is skipped: somebody is probably standing there. Before, that held only with
more than one page selected, a panel parked on another page stopped the harvest
for ever with nothing but a debug line, and a final page read that failed made
the next round walk the panel whoever was at it. Now one page counts as much as
seven, the skips are counted and after HARVEST_SKIP_LIMIT of them the harvest
goes ahead and puts the panel back where it found it, and a failed final read
keeps the best knowledge of where the panel is.

The coordinator is built against the stub Home Assistant, so these sit out a
run under a real core; the same rule under a real core is in
test_homeassistant_skips.py.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import pytest

import ha_stub
from conftest import load

HOME = 20
HISTORY = 30
OTHER = 50
INTERVAL = 1800


def run(coro):
    return asyncio.run(coro)


class FakePanel:
    """A display with one harvested page, whose panel the test can move by hand.

    Replays nothing: a walk to a page is one move, a restore is one move, and
    every move is written down. ``fail_reads_after_moves`` makes a number of
    page reads fail after the given move (counted from the first move of the
    test), which is how the final read of a round is made to fail without the
    route failing with it.
    """

    def __init__(self, web_api, start: int = HOME) -> None:
        self.page = start
        self.panel = asyncio.Lock()
        self.moves: list[int] = []
        self.unreachable: set[int] = set()
        self.fail_reads_after_moves: dict[int, int] = {}
        self._reads_to_fail = 0
        self._error = web_api.CtcWebError

    async def async_current_page(self) -> int:
        if self._reads_to_fail:
            self._reads_to_fail -= 1
            raise self._error("/vars/menu timed out")
        return self.page

    async def async_goto_page(self, target: int, route=None) -> bool:
        if target in self.unreachable:
            return False
        if self.page != target:
            self.page = target
            self.moves.append(target)
            self._reads_to_fail = self.fail_reads_after_moves.get(len(self.moves), 0)
        return True

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        return await self.async_goto_page(target)

    async def async_goto_home(self, hops: int = 6) -> int | None:
        if HOME in self.unreachable:
            return None
        await self.async_goto_page(HOME)
        return HOME

    async def async_vars(self, screen: int) -> list:
        return [123] if screen == HISTORY * 10 else []


@pytest.fixture()
def coordinator_module():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


@pytest.fixture()
def harvest(coordinator_module, web_api, const):
    """A coordinator with one page, and a hand on the stub base class's job.

    The stub base class never runs a refresh, so each round here calls the
    update directly and keeps its result as the data the next round starts
    from, which is what the real base class does between rounds.
    """
    page = const.SlowPage(
        page=HISTORY,
        title="Historik",
        screens=[HISTORY * 10],
        values=[
            const.SlowValue(
                key="p30_tillford_energi_totalt",
                label="Tillförd energi totalt",
                page=HISTORY,
                screen=HISTORY * 10,
                fmt="%.1f",
                var_indices=[0],
                unit="kWh",
                scale=0.1,
            )
        ],
        route=[(80, 255)],
    )
    panel = FakePanel(web_api)
    coordinator = coordinator_module.CtcWebCoordinator(None, panel, [page], INTERVAL)

    async def round() -> dict | None:
        try:
            coordinator.data = await coordinator._async_update_data()
        except coordinator_module.UpdateFailed:
            return None
        return coordinator.data

    return panel, coordinator, round


# ---------------------------------------------------------- the skip itself


def test_a_single_page_is_left_alone_too_when_the_panel_has_moved(harvest):
    panel, coordinator, round = harvest

    async def scenario():
        assert await round() == {"p30_tillford_energi_totalt": 12.3}
        assert panel.moves == [HISTORY, HOME], "dit och tillbaka"
        assert coordinator._expected_page == HOME

        # Somebody opens another page. With one page selected this used to go
        # unnoticed and the panel was walked under their hands.
        panel.page = OTHER
        moves = len(panel.moves)
        assert await round() == {"p30_tillford_energi_totalt": 12.3}, "värdet bärs vidare"
        assert len(panel.moves) == moves, "panelen rörs inte"
        assert coordinator.skips_in_a_row == 1
        assert coordinator.last_skip_reason == "panelen används av någon annan"

    run(scenario())


def test_after_the_limit_the_harvest_goes_ahead_and_puts_the_panel_back_where_it_stood(
    harvest, const
):
    panel, coordinator, round = harvest

    async def scenario():
        await round()
        panel.page = OTHER
        for skipped in range(1, const.HARVEST_SKIP_LIMIT + 1):
            moves = len(panel.moves)
            await round()
            assert len(panel.moves) == moves
            assert coordinator.skips_in_a_row == skipped

        # The limit is reached: the panel has been away for two rounds, which
        # is a parked panel or a failed restore rather than a person, so the
        # harvest goes ahead and returns the panel to the page it stood on.
        await round()
        assert panel.moves[-2:] == [HISTORY, OTHER], "skördad och tillbaka dit den stod"
        assert panel.page == OTHER
        assert coordinator.skips_in_a_row == 0
        assert coordinator.last_skip_reason is None
        assert coordinator._expected_page == OTHER

        # From then on that page is where the harvest expects the panel, so the
        # next round is an ordinary one and the panel goes back there again.
        await round()
        assert panel.moves[-2:] == [HISTORY, OTHER]
        assert coordinator.skips_in_a_row == 0

    run(scenario())


def test_the_harvest_resumes_as_soon_as_the_panel_is_back(harvest):
    panel, coordinator, round = harvest

    async def scenario():
        await round()
        panel.page = OTHER
        await round()
        assert coordinator.skips_in_a_row == 1
        # Whoever it was leaves the panel where the harvest left it.
        panel.page = HOME
        moves = len(panel.moves)
        await round()
        assert panel.moves[moves:] == [HISTORY, HOME]
        assert coordinator.skips_in_a_row == 0
        assert coordinator.last_skip_reason is None

    run(scenario())


# --------------------------------------------- the two lines in the log


def test_the_first_skip_and_the_resumption_are_one_info_line_each(harvest, caplog):
    panel, coordinator, round = harvest
    caplog.set_level(logging.INFO, logger="ctc_ecozenith.coordinator")

    async def scenario():
        await round()
        panel.page = OTHER
        await round()
        await round()
        panel.page = HOME
        await round()

    run(scenario())
    info = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    skipped = [line for line in info if "this round is skipped" in line]
    resumed = [line for line in info if "the harvest resumes" in line]
    assert len(skipped) == 1, "första överhoppningen sägs en gång, inte varje varv"
    assert len(resumed) == 1
    assert f"page {OTHER}" in skipped[0] and f"page {HOME}" in skipped[0]


def test_going_ahead_after_the_limit_is_said_once_too(harvest, const, caplog):
    panel, coordinator, round = harvest
    caplog.set_level(logging.INFO, logger="ctc_ecozenith.coordinator")

    async def scenario():
        await round()
        panel.page = OTHER
        for _ in range(const.HARVEST_SKIP_LIMIT + 1):
            await round()

    run(scenario())
    info = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
    assert sum("this round is skipped" in line for line in info) == 1
    ahead = [line for line in info if "goes ahead and puts it back" in line]
    assert len(ahead) == 1
    assert f"{const.HARVEST_SKIP_LIMIT} rounds" in ahead[0]


# ------------------------------------------ a final read that fails


def test_a_failed_final_read_keeps_the_page_the_panel_was_put_back_on(harvest):
    panel, coordinator, round = harvest

    async def scenario():
        # The restore is the second move of the round; the read after it fails.
        panel.fail_reads_after_moves = {2: 1}
        await round()
        assert panel.page == HOME
        assert coordinator._expected_page == HOME, "panelen sattes tillbaka, det är vad som gäller"

        # Somebody opens another page before the next round. Before, the failed
        # read had left nothing to compare with and the panel was walked anyway.
        panel.page = OTHER
        moves = len(panel.moves)
        await round()
        assert len(panel.moves) == moves, "panelen rörs inte"
        assert coordinator.skips_in_a_row == 1

    run(scenario())


def test_when_neither_the_restore_nor_the_read_works_the_old_expectation_stands(harvest):
    panel, coordinator, round = harvest

    async def scenario():
        await round()
        assert coordinator._expected_page == HOME
        # Home cannot be reached this round, and after the one move of the
        # round, to the page, neither the restore's own check nor the final
        # read gets an answer.
        panel.unreachable = {HOME}
        panel.fail_reads_after_moves = {3: 2}
        await round()
        assert panel.page == HISTORY, "panelen blev kvar på sidan"
        assert coordinator._expected_page == HOME, "ingen ny kunskap, så den gamla står kvar"
        # And the next round, finding the panel where it was left rather than
        # where it was expected, gives way rather than walks.
        panel.unreachable = set()
        moves = len(panel.moves)
        await round()
        assert len(panel.moves) == moves
        assert coordinator.skips_in_a_row == 1

    run(scenario())


# ------------------------------------------------- the moments for R34


def test_the_round_leaves_when_it_ran_and_when_the_next_is_due(harvest):
    panel, coordinator, round = harvest

    async def scenario():
        assert coordinator.last_attempt is None and coordinator.next_attempt is None
        await round()
        assert coordinator.last_attempt is not None
        assert coordinator.next_attempt - coordinator.last_attempt >= timedelta(seconds=INTERVAL)
        # A failed round is tried again sooner, and the moment says so.
        panel.unreachable = {HISTORY}

        async def fail_everything():
            raise panel._error("gone")

        panel.async_current_page = fail_everything
        await round()
        assert coordinator.patience.failures == 1
        retry = coordinator.next_attempt - coordinator.last_attempt
        assert timedelta(seconds=coordinator.patience.seconds) <= retry < timedelta(seconds=INTERVAL)

    run(scenario())
