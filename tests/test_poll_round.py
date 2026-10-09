"""The poll round ends at a lost line, learns silent blocks and times itself (R2).

Driven through the real CtcModbusClient against the pymodbus stand-in, so a
dead host and a reset in the middle of a round look the way they would in a
house, minus the waiting.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

from conftest import load
from fake_pymodbus import FakeLibrary

_HOSTS = itertools.count(1)

#: Four blocks of the real plan, enough to have something before and after a fault.
BLOCKS = [(62000, 98), (62107, 41), (62167, 7), (62191, 25)]


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="session")
def poll():
    return load("poll")


@pytest.fixture()
def quick(monkeypatch, modbus_api):
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)


@pytest.fixture()
def host() -> str:
    return f"round-{next(_HOSTS)}.test"


@pytest.fixture()
def library(monkeypatch) -> FakeLibrary:
    return FakeLibrary().install(monkeypatch)


def starts(result) -> list[int]:
    return sorted({address for address in result.raw} & {start for start, _ in BLOCKS})


# ------------------------------------------------------------------ the round


def test_a_round_reads_every_block(quick, library, modbus_api, poll, host):
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS))
    assert result.answered == [start for start, _ in BLOCKS]
    assert result.unanswered == [] and result.dropped == []
    assert len(result.raw) == sum(count for _, count in BLOCKS)
    assert result.raw[62191 + 24] == 62191 + 24
    assert result.elapsed >= 0


def test_a_dead_host_ends_the_round_at_the_first_block(quick, library, modbus_api, poll, host):
    library.controller.reachable = False
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, BLOCKS))
    assert caught.value.address == 62000
    assert "could not connect" in str(caught.value)
    assert len(library.clients) == 1, "one knock per round against a host that is away"
    assert library.alive == []


def test_a_line_found_gone_in_the_middle_ends_the_round_there(quick, library, modbus_api, poll, host):
    library.controller.gone_before.add(62167)
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, BLOCKS))
    assert caught.value.address == 62167
    assert [a for _, a, _, _ in library.requests] == [62000, 62107], "nothing after the fault is tried"
    assert library.alive == []


def test_a_reset_in_the_middle_ends_the_round_when_the_pump_stays_away(
    quick, library, modbus_api, poll, host
):
    # The controller resets under the third block and is then gone for good.
    library.controller.reset_at.add(62167)

    def pump_dies(client, kind, address):
        if address == 62167:
            library.controller.reachable = False

    library.controller.on_request = pump_dies
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, BLOCKS))
    # The reset itself is silence without a line; the next block is where the
    # round finds out the pump is away, and it says so with that address.
    assert caught.value.address == 62191
    assert len(library.clients) == 2 and library.alive == []


def test_a_reset_in_the_middle_is_survived_when_the_pump_comes_back(
    quick, library, modbus_api, poll, host
):
    library.controller.reset_at.add(62167)
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS))
    assert result.answered == [62000, 62107, 62191]
    assert result.dropped == [62167]
    assert result.unanswered == []
    assert len(library.clients) == 2, "a fresh connection for the rest of the round"
    assert library.alive == [library.clients[1]]


def test_silence_costs_only_its_block(quick, library, modbus_api, poll, host):
    library.controller.silent.add(62107)
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS))
    assert result.answered == [62000, 62167, 62191]
    assert result.unanswered == [62107]
    assert 62107 not in result.raw and 62167 in result.raw
    assert len(library.clients) == 1


def test_an_exception_code_counts_as_no_answer(quick, library, modbus_api, poll, host):
    library.controller.rejected.add(62107)
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS))
    assert result.unanswered == [62107]
    assert result.answered == [62000, 62167, 62191]


def test_blocks_learnt_as_missing_are_not_asked_for(quick, library, modbus_api, poll, host):
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS, skip={62107, 62167}))
    assert [a for _, a, _, _ in library.requests] == [62000, 62191]
    assert result.answered == [62000, 62191]


def test_a_round_with_no_answer_at_all_gives_the_line_up(quick, library, modbus_api, poll, host):
    library.controller.silent.update(start for start, _ in BLOCKS)
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, BLOCKS))
    assert caught.value.address == 62000
    assert "whole round" in str(caught.value)
    assert library.alive == [], "the client is closed so the next round starts over"
    assert len(library.requests) == len(BLOCKS), "every block was still given its chance"


def test_an_empty_plan_is_an_empty_round(quick, library, modbus_api, poll, host):
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, []))
    assert result.raw == {} and result.answered == []
    assert library.clients == [], "nothing to read means nothing to connect for"


# ------------------------------------------------------- the books it keeps


def silent_round(poll, unanswered=(), answered=(62000,), dropped=()):
    return poll.RoundResult(
        raw={a: a for a in answered},
        answered=list(answered),
        unanswered=list(unanswered),
        dropped=list(dropped),
        elapsed=1.0,
    )


def test_three_silent_rounds_teach_that_a_block_is_missing(poll):
    missing = poll.MissingBlocks()
    assert missing.note(silent_round(poll, unanswered=[62107])) == []
    assert missing.note(silent_round(poll, unanswered=[62107])) == []
    assert missing.missing == set()
    assert missing.note(silent_round(poll, unanswered=[62107])) == [62107]
    assert missing.missing == {62107}
    # Said once: a fourth silent round is not news.
    assert missing.note(silent_round(poll, unanswered=[62107])) == []


def test_a_block_that_answers_again_is_forgiven(poll):
    missing = poll.MissingBlocks()
    missing.note(silent_round(poll, unanswered=[62107]))
    missing.note(silent_round(poll, unanswered=[62107]))
    missing.note(silent_round(poll, answered=[62000, 62107]))
    assert missing.note(silent_round(poll, unanswered=[62107])) == []
    assert missing.missing == set()


def test_silence_from_everyone_is_the_line_not_the_blocks(poll):
    missing = poll.MissingBlocks()
    for _ in range(5):
        assert missing.note(silent_round(poll, unanswered=[62000, 62107], answered=[])) == []
    assert missing.missing == set()


def test_a_silence_that_took_the_line_counts_like_any_other_silence(poll):
    # pymodbus before 3.8 closes the line after every silence, so on that
    # library a block the model lacks would otherwise never be learnt. The
    # rest having answered is what tells the register from the line.
    missing = poll.MissingBlocks()
    assert missing.note(silent_round(poll, dropped=[62167])) == []
    assert missing.note(silent_round(poll, unanswered=[62167])) == []
    assert missing.note(silent_round(poll, dropped=[62167])) == [62167]
    assert missing.missing == {62167}
    # But silence from everyone, however the line went, is still the line.
    missing = poll.MissingBlocks()
    for _ in range(5):
        assert missing.note(silent_round(poll, dropped=[62000, 62167], answered=[])) == []
    assert missing.missing == set()


def test_each_block_keeps_its_own_count(poll):
    missing = poll.MissingBlocks()
    missing.note(silent_round(poll, unanswered=[62107, 62167]))
    missing.note(silent_round(poll, unanswered=[62107]))
    assert missing.note(silent_round(poll, unanswered=[62107, 62167])) == [62107]
    assert missing.note(silent_round(poll, unanswered=[62167])) == [62167]


def test_the_patience_is_three_rounds(poll):
    assert poll.MISSING_PATIENCE == 3
    assert poll.MissingBlocks().patience == 3


def test_slow_rounds_are_said_once_per_episode(poll):
    slow = poll.SlowRounds()
    assert slow.note(12.0, 30.0) is None
    assert slow.note(45.0, 30.0) == "slow"
    assert slow.note(50.0, 30.0) is None
    assert slow.note(70.0, 30.0) is None
    assert slow.note(20.0, 30.0) == "recovered"
    assert slow.note(20.0, 30.0) is None
    assert slow.note(31.0, 30.0) == "slow"


def test_slow_rounds_need_an_interval_to_compare_with(poll):
    slow = poll.SlowRounds()
    assert slow.note(45.0, 0.0) is None
    assert slow.slow is False


def test_the_round_measures_its_own_time(quick, library, modbus_api, poll, host, monkeypatch):
    import time

    def slow_controller(client, kind, address):
        time.sleep(0.01)

    library.controller.on_request = slow_controller
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, BLOCKS))
    assert result.elapsed >= 0.04 * 0.8
