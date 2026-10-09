"""The control manager under contention: writes that take time while the state moves (F2.1, F2.2, F2.3, R46).

A keepalive write waits behind the poll's lock for a moment every minute, and
in that moment the state can change under it: Släpp all styrning, a select
released, a newer value from the EMS, or the entry being unloaded. The refresh
used to record its write out of a copy taken before the wait, which put a
released address back in force without a timer, wrote an older value over a
newer one, and after an unload wrote on behalf of an entry that was gone.
These drive the real CtcControlManager over a client whose writes can be held
open, and over the real CtcModbusClient against the pymodbus stand-in for the
unload itself.
"""

from __future__ import annotations

import asyncio
import itertools
import logging

import pytest

import ha_stub
from conftest import load
from fake_pymodbus import FakeClient, FakeLibrary

EXPIRY = 300.0
_HOSTS = itertools.count(1)


def run(coro):
    return asyncio.run(coro)


class HeldClient:
    """Takes writes like CtcModbusClient; a write of a gated address waits for its gate."""

    def __init__(self, error):
        self.writes: list[tuple[int, int]] = []
        self.failing = False
        self.gates: dict[int, asyncio.Event] = {}
        self._error = error

    def hold(self, address: int) -> asyncio.Event:
        gate = self.gates[address] = asyncio.Event()
        return gate

    async def async_write(self, address: int, raw: int) -> None:
        gate = self.gates.get(address)
        if gate is not None:
            await gate.wait()
        if self.failing:
            raise self._error(f"write to {address} failed: connection reset")
        self.writes.append((address, raw))


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def coordinator():
    ha_stub.install()
    return load("coordinator")


@pytest.fixture()
def manager(coordinator, modbus_api):
    ha_stub.tracked.clear()
    client = HeldClient(modbus_api.CtcModbusError)
    clock = Clock()
    manager = coordinator.CtcControlManager(hass=object(), client=client, clock=clock)
    manager.client = client
    manager.clock = clock
    return manager


def _refresh(manager):
    """The timer's callback, as Home Assistant would call it."""
    assert ha_stub.tracked, "the keepalive timer should be running"
    action, _interval = ha_stub.tracked[-1]
    return action(None)


async def _settled() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


# ------------------------------------------------------------- the generation


def test_the_generation_moves_on_at_every_write_and_release():
    keepalive = load("keepalive").Keepalive(EXPIRY)
    assert keepalive.generation(1010) == 0
    keepalive.written(1010, 215, now=0.0)
    assert keepalive.generation(1010) == 1
    keepalive.written(1010, 225, now=60.0)
    assert keepalive.generation(1010) == 2
    assert keepalive.generation(1005) == 0, "each address has its own"
    keepalive.release(1010)
    assert keepalive.generation(1010) == 3
    keepalive.release(1010)
    assert keepalive.generation(1010) == 3, "releasing what is not in force changes nothing"
    keepalive.written(1010, 215, now=120.0)
    keepalive.written(1005, 1, now=120.0)
    keepalive.clear()
    assert keepalive.generation(1010) == 5
    assert keepalive.generation(1005) == 2
    # A miss is not a change of state: the value stands and so does the generation.
    keepalive.written(1010, 215, now=200.0)
    keepalive.failed(1010, now=260.0)
    assert keepalive.generation(1010) == 6


# ------------------------------------------------- a release under a refresh


def test_release_all_during_a_pending_refresh_wins(manager):
    """Scenario (a): the button is pressed while the refresh waits on the lock."""
    heard: list[int] = []
    manager.async_add_listener(lambda: heard.append(1))

    async def scenario():
        await manager.async_set(1010, 215)
        gate = manager.client.hold(1010)
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        manager.async_release_all()
        assert manager.active == {}
        gate.set()
        await refresh

    run(scenario())
    assert manager.active == {}, "the release stands; the late write puts nothing back"
    assert manager.written_at(1010) is None
    assert ha_stub.tracked == [], "and no timer is left running for it"
    assert manager.client.writes == [(1010, 215), (1010, 215)], "the write itself did go out"
    assert len(heard) == 2, "told at the set and at the release, not again by the refresh"


def test_releasing_one_address_during_a_pending_refresh_leaves_the_other_in_force(manager):
    async def scenario():
        await manager.async_set(1010, 215)
        await manager.async_set(1007, 2)
        gate = manager.client.hold(1010)
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        await manager.async_set(1010, None)
        gate.set()
        await refresh

    run(scenario())
    assert manager.active == {1007: 2}
    assert len(ha_stub.tracked) == 1, "the timer keeps running for what is left"
    assert manager.client.writes == [(1010, 215), (1007, 2), (1010, 215), (1007, 2)]


def test_a_release_during_a_pending_set_wins_too(manager):
    """Släpp all styrning while a set's own write is on its way: the button wins."""

    async def scenario():
        await manager.async_set(1010, 215)
        gate = manager.client.hold(1010)
        setting = asyncio.ensure_future(manager.async_set(1010, 225))
        await _settled()
        manager.async_release_all()
        gate.set()
        await setting

    run(scenario())
    assert manager.active == {}
    assert ha_stub.tracked == [], "no timer was started for a value that was released"
    assert manager.client.writes == [(1010, 215), (1010, 225)]


def test_a_release_of_another_address_during_a_set_does_not_lose_the_set(manager):
    async def scenario():
        await manager.async_set(1007, 2)
        gate = manager.client.hold(1010)
        setting = asyncio.ensure_future(manager.async_set(1010, 215))
        await _settled()
        await manager.async_set(1007, None)
        gate.set()
        await setting

    run(scenario())
    assert manager.active == {1010: 215}
    assert len(ha_stub.tracked) == 1


# ------------------------------------------------- a newer value under a refresh


def test_a_newer_value_during_a_pending_refresh_is_what_ends_up_in_the_pump(manager):
    """Scenario (b): the EMS lowers the setpoint while the refresh waits on the lock.

    The refresh used to write the older value last, and record it, so both the
    pump and Home Assistant stood on the old value until the next minute. Now
    the set waits for the refresh's write of that address and goes out after
    it, and the next refresh writes the new value.
    """

    async def scenario():
        await manager.async_set(1010, 225)
        gate = manager.client.hold(1010)
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        setting = asyncio.ensure_future(manager.async_set(1010, 205))
        await _settled()
        assert manager.client.writes == [(1010, 225)], "the set waits its turn"
        gate.set()
        await refresh
        await setting
        manager.client.gates.clear()
        await _refresh(manager)

    run(scenario())
    assert manager.client.writes == [(1010, 225), (1010, 225), (1010, 205), (1010, 205)]
    assert manager.active == {1010: 205}
    assert manager.get(1010) == 205


def test_a_refresh_writes_the_value_set_a_moment_before_it_got_its_turn(manager):
    """The other order: the set holds the lock, the refresh waits, then reads the new value."""

    async def scenario():
        await manager.async_set(1010, 225)
        gate = manager.client.hold(1010)
        setting = asyncio.ensure_future(manager.async_set(1010, 205))
        await _settled()
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        gate.set()
        await setting
        await refresh

    run(scenario())
    assert manager.client.writes == [(1010, 225), (1010, 205), (1010, 205)]
    assert manager.active == {1010: 205}


# ------------------------------------------------------ the first miss's warning


def test_a_run_of_misses_that_begins_in_a_set_warns_once_with_the_moment(manager, caplog):
    """F2.3: the warning with "until about" comes whichever path the first miss took."""
    async def scenario():
        await manager.async_set(1010, 215)
        manager.client.failing = True
        manager.clock.now += 30
        with pytest.raises(Exception):
            await manager.async_set(1010, 225)
        manager.clock.now += 30
        await _refresh(manager)
        manager.clock.now += 60
        await _refresh(manager)

    with caplog.at_level(logging.DEBUG):
        run(scenario())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "until about" in warnings[0].message
    assert "1010" in warnings[0].message
    assert manager.active == {1010: 215}, "the old value is still what the unit holds"


def test_a_failed_set_of_an_address_not_in_force_warns_nothing(manager, caplog):
    manager.client.failing = True
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(Exception):
            run(manager.async_set(1010, 215))
    assert [r for r in caplog.records if r.levelno == logging.WARNING] == []


def test_a_write_that_reaches_the_unit_again_ends_the_episode_whichever_path(manager, caplog):
    async def scenario():
        await manager.async_set(1010, 215)
        manager.client.failing = True
        manager.clock.now += 60
        await _refresh(manager)
        manager.client.failing = False
        manager.clock.now += 30
        await manager.async_set(1010, 225)
        manager.client.failing = True
        manager.clock.now += 30
        await _refresh(manager)

    with caplog.at_level(logging.DEBUG):
        run(scenario())
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2, "a new run after a recovery is news again"
    assert all("until about" in w.message for w in warnings)


# ------------------------------------------------------------- the unload


def test_after_stop_a_pending_refresh_writes_no_further_address(manager):
    """The manager's own side of F2.2: stop sets a flag the refresh checks before every write."""

    async def scenario():
        await manager.async_set(1010, 215)
        await manager.async_set(1005, 1)
        gate = manager.client.hold(1010)
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        stop = asyncio.ensure_future(manager.async_stop())
        await _settled()
        assert not stop.done(), "stop waits for the write in flight"
        gate.set()
        await stop
        assert refresh.done(), "and when stop returns the refresh is over"
        await refresh

    run(scenario())
    assert manager.client.writes == [(1010, 215), (1005, 1), (1010, 215)]
    assert manager.active == {}
    assert ha_stub.tracked == []


def test_a_set_after_stop_is_refused_and_starts_no_timer(manager, modbus_api):
    async def scenario():
        await manager.async_stop()
        with pytest.raises(modbus_api.CtcModbusError):
            await manager.async_set(1010, 215)

    run(scenario())
    assert manager.client.writes == []
    assert manager.active == {}
    assert ha_stub.tracked == []


class GatedLibraryClient(FakeClient):
    """The stand-in library client with a write that waits for a gate."""

    gates: dict[int, asyncio.Event] = {}

    async def write_registers(self, address, values, *, device_id=1):  # type: ignore[override]
        gate = self.gates.get(address)
        if gate is not None:
            await gate.wait()
        return await super().write_registers(address, values, device_id=device_id)


@pytest.fixture()
def quick(monkeypatch, modbus_api):
    monkeypatch.setattr(modbus_api, "CONNECT_DELAY", 0)
    monkeypatch.setattr(modbus_api, "MESSAGE_WAIT", 0)
    monkeypatch.setattr(modbus_api, "CLOSE_SETTLE", 0)


def test_an_unload_in_the_middle_of_a_refresh_with_two_addresses_leaves_one_closed_client(
    quick, coordinator, modbus_api, monkeypatch
):
    """The whole of F2.2, with the real client: async_stop, then async_shutdown, as the entry does.

    Two overrides in force, the refresh's first write in flight when the entry
    is unloaded. Exactly one library client is ever built, nothing is open
    afterwards, and the second address is not written again.
    """
    GatedLibraryClient.gates = {}
    library = FakeLibrary(client_class=GatedLibraryClient).install(monkeypatch)
    host = f"unload-{next(_HOSTS)}.test"
    ha_stub.tracked.clear()
    client = modbus_api.CtcModbusClient(host)
    manager = coordinator.CtcControlManager(hass=object(), client=client, clock=Clock())

    async def unload():
        await manager.async_stop()
        await client.async_shutdown()

    async def scenario():
        await manager.async_set(1010, 215)
        await manager.async_set(1005, 1)
        gate = GatedLibraryClient.gates[1010] = asyncio.Event()
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        unloading = asyncio.ensure_future(unload())
        await _settled()
        gate.set()
        await unloading
        await refresh

    run(scenario())
    assert len(library.clients) == 1
    assert library.alive == []
    assert library.controller.written == [(1010, [215]), (1005, [1]), (1010, [215])]
    assert manager.active == {}
    assert ha_stub.tracked == []


def test_a_shutdown_alone_still_stops_a_refresh_from_building_a_second_client(
    quick, coordinator, modbus_api, monkeypatch
):
    """Belt and braces: without the manager's stop, the client's own flag holds the line."""
    GatedLibraryClient.gates = {}
    library = FakeLibrary(client_class=GatedLibraryClient).install(monkeypatch)
    host = f"unload-{next(_HOSTS)}.test"
    ha_stub.tracked.clear()
    client = modbus_api.CtcModbusClient(host)
    manager = coordinator.CtcControlManager(hass=object(), client=client, clock=Clock())

    async def scenario():
        await manager.async_set(1010, 215)
        await manager.async_set(1005, 1)
        gate = GatedLibraryClient.gates[1010] = asyncio.Event()
        refresh = asyncio.ensure_future(_refresh(manager))
        await _settled()
        shutdown = asyncio.ensure_future(client.async_shutdown())
        await _settled()
        gate.set()
        await shutdown
        await refresh

    run(scenario())
    assert len(library.clients) == 1
    assert library.alive == []
    assert library.controller.written == [(1010, [215]), (1005, [1]), (1010, [215])]
