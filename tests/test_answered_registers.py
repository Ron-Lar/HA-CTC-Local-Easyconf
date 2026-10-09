"""Which registers answered is read off the raw words, not the decoded data.

The coordinator leaves a key out of its data both when a block was silent this
round and when a 32 bit pair decoded to CTC's marker for a counter that is not
fitted. Read off the data, "register 62341 answered" therefore said no in both
cases, and the set-up, which asked once, built no Modbus route for consumed
energy on an i360 whose first poll happened to miss the block, until somebody
reloaded the entry. These tests drive the real coordinator through scripted
rounds and pin where the question is answered now: a set of raw addresses it
keeps across the run.
"""

from __future__ import annotations

import asyncio

import pytest

import ha_stub
from conftest import load

MARKER = 0xFFFF
ENERGY = 62341
FIRST = 62000


@pytest.fixture(scope="module")
def coordinator_module():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


@pytest.fixture(scope="module")
def poll():
    return load("poll")


@pytest.fixture(scope="module")
def blocks(coordinator_module, modbus_api) -> list[tuple[int, int]]:
    """The register blocks the coordinator plans, from a throwaway instance."""
    client = modbus_api.CtcModbusClient("192.0.2.55")
    return list(coordinator_module.CtcModbusCoordinator(hass=object(), client=client, interval=30)._blocks)


def _block_for(blocks, address: int) -> tuple[int, int]:
    return next((s, n) for s, n in blocks if s <= address < s + n)


def _words(blocks, address: int, overrides: dict[int, int] | None = None) -> dict[int, int]:
    """The whole block holding ``address`` answered, zeros except the overrides."""
    start, count = _block_for(blocks, address)
    raw = {a: 0 for a in range(start, start + count)}
    raw.update(overrides or {})
    return raw


def _scripted(coordinator_module, poll, modbus_api, monkeypatch, rounds):
    """A coordinator whose rounds are scripted: each item is one round's raw words."""
    client = modbus_api.CtcModbusClient("192.0.2.55")
    coordinator = coordinator_module.CtcModbusCoordinator(hass=object(), client=client, interval=30)
    queue = [dict(raw) for raw in rounds]

    async def read_round(client, planned, skip=(), **_kwargs):
        raw = queue.pop(0)
        return poll.RoundResult(raw=raw, answered=[s for s, _n in planned if s in raw])

    monkeypatch.setattr(coordinator_module, "read_round", read_round)
    return coordinator


def _round(coordinator) -> dict:
    return asyncio.run(coordinator._async_update_data())


def test_the_energy_block_is_not_the_first_block(blocks):
    # The tests below need two different blocks to play against each other.
    assert _block_for(blocks, ENERGY)[0] != _block_for(blocks, FIRST)[0]


def test_a_pair_that_reads_the_marker_is_an_answer_without_a_reading(
    coordinator_module, poll, modbus_api, blocks, cop, monkeypatch
):
    marked = _words(blocks, ENERGY, {ENERGY: MARKER, ENERGY + 1: MARKER})
    coordinator = _scripted(coordinator_module, poll, modbus_api, monkeypatch, [marked])
    data = _round(coordinator)
    # The decoder throws the marker away, so the data has no key for it...
    assert "compressor_kwh" not in data
    assert cop.modbus_consumption(data) is None
    # ...and yet the register answered: the controller said "not fitted".
    assert ENERGY in coordinator.answered
    assert cop.modbus_consumption_answered(coordinator.answered) is True


def test_a_zero_is_both_an_answer_and_a_reading(
    coordinator_module, poll, modbus_api, blocks, cop, monkeypatch
):
    coordinator = _scripted(coordinator_module, poll, modbus_api, monkeypatch, [_words(blocks, ENERGY)])
    data = _round(coordinator)
    assert data["compressor_kwh"] == 0.0
    assert cop.modbus_consumption(data) == 0.0
    assert cop.modbus_consumption_answered(coordinator.answered) is True


def test_a_block_silent_this_round_keeps_an_earlier_answer(
    coordinator_module, poll, modbus_api, blocks, cop, monkeypatch
):
    coordinator = _scripted(
        coordinator_module, poll, modbus_api, monkeypatch,
        [_words(blocks, ENERGY), _words(blocks, FIRST)],
    )
    assert "compressor_kwh" in _round(coordinator)
    # Second round: another block answers, the energy block is quiet. The data
    # of that round has no key, as before, but the answer already given stands.
    data = _round(coordinator)
    assert "compressor_kwh" not in data
    assert cop.modbus_consumption(data) is None
    assert cop.modbus_consumption_answered(coordinator.answered) is True


def test_a_register_that_never_answered_is_not_answered(
    coordinator_module, poll, modbus_api, blocks, cop, monkeypatch
):
    coordinator = _scripted(
        coordinator_module, poll, modbus_api, monkeypatch,
        [_words(blocks, FIRST), _words(blocks, FIRST)],
    )
    _round(coordinator)
    _round(coordinator)
    assert FIRST in coordinator.answered
    assert ENERGY not in coordinator.answered
    assert cop.modbus_consumption_answered(coordinator.answered) is False


# --------------------------------------------- what the sensors say meanwhile


def test_the_reason_names_the_register_while_it_has_not_answered(cop):
    # The sensors exist from the start on an i360, so while the register has
    # not answered they have to say so, rather than "not read yet" for ever.
    silent = cop.cop_reason(None, "lifetime", 12000.0, None, 20000, modbus_answered=False)
    assert "62341" in silent and "inte svarat" in silent
    assert "62341" in cop.cop_reason(None, "day", None, None, modbus_answered=False)
    assert "62341" in cop.cop_reason(None, "first_year", None, None, modbus_answered=False)
    # Once it has answered, the ordinary reasons take over.
    assert "inte lästs" in cop.cop_reason(None, "lifetime", 12000.0, None, 20000, modbus_answered=True)
    assert "20 till 30 timmar" in cop.cop_reason(None, "day", None, None, modbus_answered=True)
    # A consumed total that is there was read, whatever Modbus is doing.
    assert "för lite energi" in cop.cop_reason(None, "lifetime", 5.0, 4.0, 2, modbus_answered=False)
