"""The control registers' keepalive says what the controller actually holds.

An override used to be recorded before the write, so a failed write showed as
"styrning aktiv: ja" with nothing written, and a refresh that kept failing
warned once a minute per register for as long as Home Assistant ran, long after
the controller had forgotten the value. Now a value is in force from a write
that reached the unit, for as long as one has reached it within five minutes,
and the log gets a line when that changes rather than every minute.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

import ha_stub
from conftest import load

EXPIRY = 300.0


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- the rule


@pytest.fixture()
def keepalive():
    return load("keepalive").Keepalive(EXPIRY)


def test_a_write_that_reached_the_unit_is_in_force_with_its_moment(keepalive):
    assert keepalive.written(1002, 450, now=1000.0) is False
    assert keepalive.active == {1002: 450}
    assert keepalive.get(1002) == 450
    assert keepalive.written_at(1002) == 1000.0
    assert keepalive.valid_until(1002) == 1000.0 + EXPIRY
    assert bool(keepalive)


def test_nothing_is_in_force_until_something_is_written(keepalive):
    assert keepalive.active == {}
    assert keepalive.written_at(1002) is None
    assert keepalive.valid_until(1002) is None
    assert not keepalive


def test_three_misses_are_three_minutes_and_the_unit_still_holds_it(keepalive):
    keepalive.written(1002, 450, now=0.0)
    for minute in (60.0, 120.0, 180.0, 240.0):
        assert keepalive.failed(1002, now=minute) is False, minute
        assert keepalive.get(1002) == 450
    assert keepalive.failures(1002) == 4


def test_at_the_expiry_the_address_is_given_up(keepalive):
    keepalive.written(1002, 450, now=0.0)
    assert keepalive.failed(1002, now=EXPIRY) is True
    assert keepalive.active == {}
    assert keepalive.written_at(1002) is None
    assert keepalive.failures(1002) == 0
    assert not keepalive


def test_time_decides_not_the_count(keepalive):
    """One miss after a long pause is enough: the controller has forgotten it."""
    keepalive.written(1002, 450, now=0.0)
    assert keepalive.failed(1002, now=EXPIRY + 100) is True
    assert keepalive.active == {}


def test_a_write_that_reaches_the_unit_again_is_a_recovery(keepalive):
    keepalive.written(1002, 450, now=0.0)
    keepalive.failed(1002, now=60.0)
    keepalive.failed(1002, now=120.0)
    assert keepalive.written(1002, 450, now=180.0) is True
    assert keepalive.failures(1002) == 0
    assert keepalive.written_at(1002) == 180.0
    # And the clock starts over from the write that reached it.
    assert keepalive.failed(1002, now=180.0 + EXPIRY - 1) is False
    assert keepalive.failed(1002, now=180.0 + EXPIRY) is True


def test_a_miss_on_an_address_not_in_force_claims_nothing(keepalive):
    assert keepalive.failed(1007, now=10.0) is False
    assert keepalive.failures(1007) == 0
    assert keepalive.active == {}


def test_each_address_keeps_its_own_time(keepalive):
    keepalive.written(1002, 450, now=0.0)
    keepalive.written(1007, 2, now=200.0)
    assert keepalive.failed(1002, now=EXPIRY) is True
    assert keepalive.failed(1007, now=EXPIRY) is False
    assert keepalive.active == {1007: 2}


def test_release_and_clear_forget_the_moments_too(keepalive):
    keepalive.written(1002, 450, now=0.0)
    keepalive.written(1007, 2, now=0.0)
    assert keepalive.release(1002) is True
    assert keepalive.release(1002) is False
    assert keepalive.written_at(1002) is None
    keepalive.clear()
    assert keepalive.active == {}
    assert keepalive.valid_until(1007) is None


# ------------------------------------------------- the manager around it


class FakeClient:
    """Takes writes like CtcModbusClient, and fails when told to."""

    def __init__(self, error):
        self.writes: list[tuple[int, int]] = []
        self.failing = False
        self._error = error

    async def async_write(self, address: int, raw: int) -> None:
        if self.failing:
            raise self._error(f"write to {address} failed: connection reset")
        self.writes.append((address, raw))


class Clock:
    def __init__(self, now: float = 1_700_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def manager(modbus_api):
    ha_stub.install()
    coordinator = load("coordinator")
    ha_stub.tracked.clear()
    client = FakeClient(modbus_api.CtcModbusError)
    clock = Clock()
    manager = coordinator.CtcControlManager(hass=object(), client=client, clock=clock)
    manager.client = client
    manager.clock = clock
    return manager


def _refresh(manager):
    assert ha_stub.tracked, "the keepalive timer should be running"
    action, interval = ha_stub.tracked[-1]
    assert interval.total_seconds() == 60
    run(action(None))


def test_a_failed_write_is_not_control_in_force(manager, modbus_api):
    manager.client.failing = True
    with pytest.raises(modbus_api.CtcModbusError):
        run(manager.async_set(1002, 450))
    assert manager.active == {}
    assert manager.written_at(1002) is None
    assert ha_stub.tracked == [], "no timer for nothing"


def test_a_write_that_reached_the_unit_starts_the_timer_and_stamps_the_moment(manager):
    run(manager.async_set(1002, 450))
    assert manager.active == {1002: 450}
    assert len(ha_stub.tracked) == 1
    assert manager.written_at(1002).timestamp() == manager.clock.now
    assert manager.valid_until(1002).timestamp() == manager.clock.now + EXPIRY


def test_the_refresh_writes_again_and_moves_the_moment(manager):
    run(manager.async_set(1002, 450))
    manager.clock.now += 60
    _refresh(manager)
    assert manager.client.writes == [(1002, 450), (1002, 450)]
    assert manager.written_at(1002).timestamp() == manager.clock.now


def test_misses_are_one_warning_then_quiet_until_the_release(manager, caplog):
    heard: list[int] = []
    manager.async_add_listener(lambda: heard.append(1))
    run(manager.async_set(1002, 450))
    manager.client.failing = True
    with caplog.at_level(logging.DEBUG):
        for minute in range(1, 5):
            manager.clock.now += 60
            _refresh(manager)
            assert manager.active == {1002: 450}, minute
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1
        assert "until about" in warnings[0].message
        caplog.clear()
        manager.clock.now += 60   # five minutes since the last write that reached it
        _refresh(manager)
    assert manager.active == {}
    assert manager.written_at(1002) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "released" in warnings[0].message
    assert ha_stub.tracked == [], "nothing left to keep alive, so the timer stops"
    # The entities were told at the write and at the release, not per miss.
    assert len(heard) == 2


def test_a_write_that_reaches_the_unit_again_is_one_info_line(manager, caplog):
    run(manager.async_set(1002, 450))
    manager.client.failing = True
    manager.clock.now += 60
    _refresh(manager)
    manager.client.failing = False
    manager.clock.now += 60
    with caplog.at_level(logging.INFO):
        _refresh(manager)
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert len(infos) == 1
    assert "again" in infos[0].message
    assert manager.active == {1002: 450}
    assert manager.written_at(1002).timestamp() == manager.clock.now


def test_other_registers_stay_in_force_when_one_is_given_up(manager):
    run(manager.async_set(1002, 450))
    manager.clock.now += 200
    run(manager.async_set(1007, 2))
    manager.client.failing = True
    manager.clock.now += 100
    _refresh(manager)
    assert manager.active == {1007: 2}
    assert len(ha_stub.tracked) == 1


def test_release_all_stops_the_timer_without_writing(manager):
    run(manager.async_set(1002, 450))
    manager.async_release_all()
    assert manager.active == {}
    assert ha_stub.tracked == []
    assert manager.client.writes == [(1002, 450)]


def test_releasing_the_last_register_stops_the_timer(manager):
    run(manager.async_set(1002, 450))
    run(manager.async_set(1002, None))
    assert ha_stub.tracked == []


# ------------------------------------------------- what the entities show


class FakeCoordinator:
    def __init__(self, descriptions):
        self.descriptions = descriptions
        self.data = {}
        self.last_update_success = True


class FakeRuntime:
    device = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}

    def __init__(self, control, const):
        self.control = control
        self.modbus = FakeCoordinator(const.MODBUS_SENSORS + const.MODBUS_SETTINGS)


def _register(const, key):
    return next(r for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS if r.key == key)


def test_the_number_shows_when_it_was_written_and_until_when_it_holds(manager, const):
    number = load("number")
    entity = number.CtcControlNumber(FakeRuntime(manager, const), _register(const, "ctl_max_rps"))
    idle = entity.extra_state_attributes
    assert idle["styrning aktiv"] == "nej"
    assert "senast skriven" not in idle and "gäller till" not in idle
    run(manager.async_set(1002, 450))
    busy = entity.extra_state_attributes
    assert busy["styrning aktiv"] == "ja"
    assert busy["senast skriven"] == manager.written_at(1002).isoformat(timespec="seconds")
    assert busy["gäller till"] == manager.valid_until(1002).isoformat(timespec="seconds")
    assert busy["gäller till"] > busy["senast skriven"]


def test_the_select_shows_the_same_two(manager, const):
    select = load("select")
    entity = select.CtcControlSelect(FakeRuntime(manager, const), _register(const, "ctl_dhw_mode"))
    assert "senast skriven" not in entity.extra_state_attributes
    assert entity.current_option == select.RELEASE
    run(manager.async_set(1007, 2))
    assert entity.current_option == "Komfort"
    shown = entity.extra_state_attributes
    assert shown["senast skriven"] == manager.written_at(1007).isoformat(timespec="seconds")
    assert shown["gäller till"] == manager.valid_until(1007).isoformat(timespec="seconds")


def test_after_the_release_the_entities_are_idle_again(manager, const):
    number = load("number")
    entity = number.CtcControlNumber(FakeRuntime(manager, const), _register(const, "ctl_max_rps"))
    run(manager.async_set(1002, 450))
    manager.client.failing = True
    manager.clock.now += EXPIRY
    _refresh(manager)
    shown = entity.extra_state_attributes
    assert shown["styrning aktiv"] == "nej"
    assert "senast skriven" not in shown
