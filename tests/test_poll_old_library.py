"""The poll round on the library versions the manifest allows (F1.2, R2).

pymodbus 3.6.9, the manifest's floor, closes the connection after every silent
request. A block the model lacks was then never learnt as missing, because the
round filed the silence under the line rather than the register, and every
round paid a timeout, a settle and a new connection for it, for ever. Driven
through the real CtcModbusClient against the pymodbus stand-in.
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
            run(poll.read_round(client, plan, missing.missing))
    assert missing.missing == set()
