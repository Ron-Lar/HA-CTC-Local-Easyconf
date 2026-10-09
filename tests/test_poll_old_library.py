"""The poll round on the library versions the manifest allows, and against a pump that says nothing (F1.2, F1.4, R2).

pymodbus 3.6.9, the manifest's floor, closes the connection after every silent
request. A block the model lacks was then never learnt as missing, because the
round filed the silence under the line rather than the register, and every
round paid a timeout, a settle and a new connection for it, for ever. And a
pump whose TCP side answers while its Modbus side is silent used to cost a
timeout per block before the round gave up, two to four times the interval.
Driven through the real CtcModbusClient against the pymodbus stand-in.
"""

from __future__ import annotations

import asyncio
import itertools

import pytest

from conftest import load
from fake_pymodbus import FakeLibrary, OldFakeClient

_HOSTS = itertools.count(1)

#: The front of the real plan: the four blocks in the stored block, which some
#: models lack, and then the probe block that every model answers.
I360_PLAN = [(61500, 22), (61542, 1), (61572, 1), (61590, 2), (62000, 98), (62107, 41)]
STORED = {61500, 61542, 61572, 61590}


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
    return f"old-{next(_HOSTS)}.test"


@pytest.fixture()
def old_library(monkeypatch) -> FakeLibrary:
    """pymodbus at the floor: the old keyword, and the line closed after every silence."""
    library = FakeLibrary(version="3.6.9", client_class=OldFakeClient).install(monkeypatch)
    library.controller.closes_after_silence = True
    return library


@pytest.fixture()
def library(monkeypatch) -> FakeLibrary:
    return FakeLibrary().install(monkeypatch)


# ----------------------------------------------- learning on the old library


def test_on_the_old_library_a_missing_block_is_learnt_and_then_costs_nothing(
    quick, old_library, modbus_api, poll, host
):
    old_library.controller.silent.add(61500)
    client = modbus_api.CtcModbusClient(host)
    missing = poll.MissingBlocks()
    plan = [(61500, 22), (62000, 98)]

    async def scenario():
        learnt = []
        for _ in range(3):
            result = await poll.read_round(client, plan, missing.missing)
            assert result.dropped == [61500], "the library closed the line after the silence"
            assert result.answered == [62000]
            learnt.append(missing.note(result))
        return learnt

    assert run(scenario()) == [[], [], [61500]]
    assert missing.missing == {61500}
    # Every round so far reconnected after the silence: four clients in all.
    assert len(old_library.clients) == 4
    before = len(old_library.clients)
    result = run(poll.read_round(client, plan, missing.missing))
    assert result.answered == [62000] and result.dropped == []
    assert len(old_library.clients) == before, "a learnt block costs no connection"
    assert old_library.alive == [old_library.clients[-1]]


def test_on_the_old_library_silence_from_everyone_is_still_the_line(
    quick, old_library, modbus_api, poll, host
):
    old_library.controller.silent.update({61500, 62000})
    client = modbus_api.CtcModbusClient(host)
    missing = poll.MissingBlocks()
    plan = [(61500, 22), (62000, 98)]
    for _ in range(3):
        with pytest.raises(modbus_api.CtcModbusTransportError):
            run(poll.read_round(client, plan, missing.missing, probe=62000))
    assert missing.missing == set()


# ------------------------------------------------ a pump that says nothing


def test_the_probe_block_leads_the_round(poll):
    assert poll.lead_with(I360_PLAN, 62000) == [
        (62000, 98), (61500, 22), (61542, 1), (61572, 1), (61590, 2), (62107, 41),
    ]
    # A register inside a block, not at its start, finds its block too.
    assert poll.lead_with(I360_PLAN, 62050)[0] == (62000, 98)
    # Nothing to lead with leaves the plan alone.
    assert poll.lead_with(I360_PLAN, 63000) == I360_PLAN
    assert poll.lead_with([], 62000) == []


def test_a_silent_probe_block_gives_the_line_up_after_one_request(quick, library, modbus_api, poll, host):
    """TCP up, Modbus silent: a hung Modbus task in the pump, or the pump cut without a reset."""
    library.controller.silent.update(start for start, _ in I360_PLAN)
    client = modbus_api.CtcModbusClient(host)
    plan = poll.lead_with(I360_PLAN, 62000)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, plan, probe=62000))
    assert caught.value.address == 62000
    assert "62000" in str(caught.value)
    assert len(library.requests) == 1, "one timeout, not one per block"
    assert library.alive == [], "the client is closed so the next round starts over"


def test_a_silent_probe_block_gives_the_line_up_wherever_it_sits_while_nothing_answered(
    quick, library, modbus_api, poll, host
):
    library.controller.silent.update(start for start, _ in I360_PLAN)
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError) as caught:
        run(poll.read_round(client, I360_PLAN, probe=62000))
    assert caught.value.address == 62000
    assert [a for _, a, _, _ in library.requests] == [61500, 61542, 61572, 61590, 62000]


def test_a_silent_probe_block_after_an_answer_is_only_its_block(quick, library, modbus_api, poll, host):
    library.controller.silent.add(62000)
    client = modbus_api.CtcModbusClient(host)
    result = run(poll.read_round(client, I360_PLAN, probe=62000))
    assert result.unanswered == [62000]
    assert result.answered == [61500, 61542, 61572, 61590, 62107]
    assert len(library.clients) == 1 and library.clients[0].connected


def test_without_a_probe_the_round_still_gives_every_block_its_chance(quick, library, modbus_api, poll, host):
    library.controller.silent.update(start for start, _ in I360_PLAN)
    client = modbus_api.CtcModbusClient(host)
    with pytest.raises(modbus_api.CtcModbusTransportError):
        run(poll.read_round(client, I360_PLAN))
    assert len(library.requests) == len(I360_PLAN)


def test_a_model_without_the_stored_block_still_learns_it_with_the_probe_first(
    quick, library, modbus_api, poll, host
):
    """The i360: four silent blocks, learnt in three rounds, and the round is never cut short."""
    library.controller.silent.update(STORED)
    client = modbus_api.CtcModbusClient(host)
    missing = poll.MissingBlocks()
    plan = poll.lead_with(I360_PLAN, 62000)

    async def scenario():
        learnt = []
        for _ in range(3):
            result = await poll.read_round(client, plan, missing.missing, probe=62000)
            assert result.answered == [62000, 62107]
            assert sorted(result.unanswered) == sorted(STORED)
            learnt.append(sorted(missing.note(result)))
        result = await poll.read_round(client, plan, missing.missing, probe=62000)
        return learnt, result

    learnt, last = run(scenario())
    assert learnt == [[], [], sorted(STORED)]
    assert missing.missing == STORED
    assert last.answered == [62000, 62107] and last.unanswered == []
    assert len(library.clients) == 1, "silence with the line up never cost the connection"


def test_the_same_model_on_the_old_library_learns_it_too(quick, old_library, modbus_api, poll, host):
    old_library.controller.silent.update(STORED)
    client = modbus_api.CtcModbusClient(host)
    missing = poll.MissingBlocks()
    plan = poll.lead_with(I360_PLAN, 62000)

    async def scenario():
        for _ in range(3):
            result = await poll.read_round(client, plan, missing.missing, probe=62000)
            assert 62000 in result.answered
            missing.note(result)
        return await poll.read_round(client, plan, missing.missing, probe=62000)

    last = run(scenario())
    assert missing.missing == STORED
    assert last.answered == [62000, 62107] and last.dropped == []


def test_the_coordinator_leads_with_the_probe_block_and_hands_it_to_the_round(poll, modbus_api):
    import ha_stub

    ha_stub.skip_unless_stubbed()
    coordinator = load("coordinator")
    const = load("const")

    class NoClient:
        pass

    modbus = coordinator.CtcModbusCoordinator(hass=object(), client=NoClient(), interval=30)
    assert modbus._blocks[0][0] <= modbus_api.PROBE_REGISTER < sum(modbus._blocks[0])
    assert sorted(modbus._blocks) == modbus_api.plan_blocks(const.MODBUS_SENSORS + const.MODBUS_SETTINGS)
    assert modbus_api.PROBE_REGISTER == 62000
