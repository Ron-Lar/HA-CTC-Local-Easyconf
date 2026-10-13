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
from datetime import timedelta

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


def test_a_release_is_counted_whether_or_not_the_address_was_held(keepalive):
    """F1.2: the write that would put it in force may be on its way."""
    assert keepalive.releases(1010) == 0
    assert keepalive.release(1010) is False
    assert keepalive.releases(1010) == 1
    keepalive.written(1010, 215, now=0.0)
    assert keepalive.release(1010) is True
    assert keepalive.releases(1010) == 2
    assert keepalive.releases(1007) == 0, "each address has its own"


def test_letting_go_of_everything_moves_the_epoch_even_with_nothing_held(keepalive):
    assert keepalive.epoch == 0
    keepalive.clear()
    assert keepalive.epoch == 1
    keepalive.written(1010, 215, now=0.0)
    keepalive.clear()
    assert keepalive.epoch == 2
    assert keepalive.releases(1010) == 0, "the epoch says it, not the count per address"


def test_giving_up_a_forgotten_address_is_no_release(keepalive):
    """Nobody let go of it, so a newer value on its way is still wanted."""
    keepalive.written(1010, 215, now=0.0)
    assert keepalive.failed(1010, now=EXPIRY) is True
    keepalive.written(1007, 2, now=0.0)
    assert keepalive.expire(1007, now=EXPIRY) is True
    assert keepalive.releases(1010) == 0 and keepalive.releases(1007) == 0
    assert keepalive.epoch == 0


def test_the_check_at_the_expiry_gives_up_only_what_the_unit_has_forgotten(keepalive):
    """F2.2: the same rule as a miss, at the moment itself, and not counted as one."""
    keepalive.written(1010, 215, now=40.0)
    assert keepalive.expire(1010, now=40.0 + EXPIRY - 1) is False
    assert keepalive.active == {1010: 215}
    assert keepalive.failures(1010) == 0, "a check is no miss"
    assert keepalive.expire(1010, now=40.0 + EXPIRY) is True
    assert keepalive.active == {}
    assert keepalive.written_at(1010) is None
    assert keepalive.expire(1010, now=1000.0) is False, "nothing left to give up"


# ------------------------------------------------- the manager around it

#: Every (action, moment) the manager asked to have run once, the check at an
#: override's expiry (F2.2), as ha_stub.tracked holds the keepalive's interval.
expiries: list[tuple] = []


def record_expiries(monkeypatch) -> None:
    """Have the check at an override's expiry recorded in ``expiries`` instead of started.

    The manager imports ``async_track_point_in_time`` from the event helper
    when it arms the check, so patching the module attribute serves the
    stand-in, which has none, and a real core, whose timer would want
    ``hass.loop``. Call after ha_stub.record_timers, which installs the
    stand-in. The record starts empty.
    """
    import homeassistant.helpers.event as event

    def track(hass, action, moment):
        entry = (action, moment)
        expiries.append(entry)

        def _unsub() -> None:
            if entry in expiries:
                expiries.remove(entry)

        return _unsub

    monkeypatch.setattr(event, "async_track_point_in_time", track, raising=False)
    expiries.clear()


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
def manager(modbus_api, monkeypatch):
    ha_stub.record_timers(monkeypatch)
    record_expiries(monkeypatch)
    coordinator = load("coordinator")
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


# ------------------------------------------- the check at the expiry (F2.2)


def _expire(manager):
    """The check at the expiry, as Home Assistant would call it at its moment: once."""
    assert len(expiries) == 1, "one check should be armed"
    action, moment = expiries.pop()
    action(moment)


def test_a_write_between_two_ticks_is_given_up_at_its_expiry_not_at_the_next_tick(manager, caplog):
    """The keepalive's minute is set by the first set; a later set is out of step with it.

    The value is set again 40 s after the timer began, the line goes 5 s
    later, and the ticks miss on the minute. At the fifth the last write that
    reached the unit is 260 s old, so the tick keeps it; the check armed at
    the first miss gives it up at ok_at plus 300 s, 20 s before the sixth.
    """
    heard: list[int] = []
    manager.async_add_listener(lambda: heard.append(1))
    start = manager.clock.now
    run(manager.async_set(1010, 215))
    manager.clock.now = start + 40
    run(manager.async_set(1010, 205))
    ok_at = manager.written_at(1010)
    manager.client.failing = True
    with caplog.at_level(logging.DEBUG):
        for minute in range(1, 6):
            manager.clock.now = start + 60 * minute
            _refresh(manager)
            assert manager.active == {1010: 205}, minute
        # Armed once, at the first miss, for the moment the unit lets go: a
        # microsecond past it, so the rounding to microseconds never fires it
        # before the manager's clock has reached it.
        (_action, moment) = expiries[0]
        assert moment - manager.valid_until(1010) == timedelta(microseconds=1)
        assert moment - ok_at == timedelta(seconds=EXPIRY, microseconds=1)
        caplog.clear()
        manager.clock.now = moment.timestamp()
        _expire(manager)
    assert manager.active == {}
    assert manager.written_at(1010) is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "released" in warnings[0].message
    assert ha_stub.tracked == [], "nothing left to keep alive, so the timer stops"
    assert expiries == []
    assert len(heard) == 3, "told at the two sets and at the release, not per miss"


def test_the_check_leaves_an_override_that_a_write_reached_since(manager):
    run(manager.async_set(1010, 215))
    manager.client.failing = True
    manager.clock.now += 60
    _refresh(manager)
    (action, moment) = expiries[0]
    manager.client.failing = False
    manager.clock.now += 60
    _refresh(manager)
    assert expiries == [], "a write that reached the unit disarms it"
    # Even if it fired all the same, it finds the newer write and leaves it.
    manager.clock.now = moment.timestamp()
    action(moment)
    assert manager.active == {1010: 215}


def test_a_release_release_all_and_the_entry_going_disarm_the_check(manager):
    def armed():
        run(manager.async_set(1010, 215))
        manager.client.failing = True
        manager.clock.now += 60
        _refresh(manager)
        manager.client.failing = False
        assert len(expiries) == 1

    armed()
    run(manager.async_set(1010, None))
    assert expiries == []
    armed()
    manager.async_release_all()
    assert expiries == []
    armed()
    run(manager.async_stop())
    assert expiries == []


def test_a_first_miss_in_a_set_arms_the_check_too(manager, modbus_api):
    run(manager.async_set(1010, 215))
    manager.client.failing = True
    manager.clock.now += 30
    with pytest.raises(modbus_api.CtcModbusError):
        run(manager.async_set(1010, 205))
    (_action, moment) = expiries[0]
    assert moment - manager.valid_until(1010) == timedelta(microseconds=1)
    manager.clock.now = moment.timestamp()
    _expire(manager)
    assert manager.active == {}


def test_a_check_that_comes_a_moment_early_by_the_managers_clock_gives_up_nothing(manager):
    """The next miss gives the address up then, as before the check."""
    run(manager.async_set(1010, 215))
    manager.client.failing = True
    manager.clock.now += 60
    _refresh(manager)
    (_action, moment) = expiries[0]
    manager.clock.now = moment.timestamp() - 1
    _expire(manager)
    assert manager.active == {1010: 215}
    manager.clock.now = moment.timestamp() + 59
    _refresh(manager)
    assert manager.active == {}


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
