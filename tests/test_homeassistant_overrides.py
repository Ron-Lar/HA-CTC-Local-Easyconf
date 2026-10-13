"""What Home Assistant holds, under a real Home Assistant core (roadmap R49).

The sensor Aktiva överstyrningar follows every write, release and override
given up, at the moment the pump lets go, and stands still through the
keepalive; the hot water mode shows the pump's own program from 61500 while
idle, carries it beside a held mode, and still writes a mode the list already
shows; and a lost Modbus round leaves every control available, so an action
that sets a value or picks a mode is never dropped for it, with an unread
61500 shown as unknown. What can be read without a core is in
test_overrides.py.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_overrides.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.const import EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    READINGS,
    FakeModbus,
    _advance,
    _entity_id,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)

from custom_components.ctc_ecozenith.const import (
    CONTROL_EXPIRY_SECONDS,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
)
from custom_components.ctc_ecozenith.select import RELEASE


@pytest.fixture(autouse=True)
def _hot_water_mode(monkeypatch):
    """The pump's own hot water program, Normal, in the stored block."""
    monkeypatch.setitem(READINGS, 61500, 1)


async def test_the_sensor_follows_every_write_and_release(hass):
    entry = await _set_up(hass)
    sensor = _entity_id(hass, "sensor", "active_overrides")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    mode = _entity_id(hass, "select", "ctl_dhw_mode")
    state = hass.states.get(sensor)
    assert state.state == "0"
    assert state.attributes["styrningar"] == [] and state.attributes["overrides"] == []

    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    state = hass.states.get(sensor)
    assert state.state == "1"
    (held,) = state.attributes["overrides"]
    assert (
        held["name"] == "Styr rumsbörvärde" and held["register"] == "1010" and held["value"] == 21.5
    )
    # Set by this write, which is also the last that reached the pump (F6.2).
    assert held["set_at"] == hass.states.get(room).attributes["last_written"]
    assert state.attributes["styrningar"][0]["satt"] == held["set_at"]

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": mode, "option": "Komfort"}, blocking=True)
    state = hass.states.get(sensor)
    assert state.state == "2"
    assert [item["namn"] for item in state.attributes["styrningar"]] == [
        "Styr rumsbörvärde", "Styr varmvattenläge",
    ]
    assert state.attributes["styrningar"][1]["värde"] == "Komfort"

    await hass.services.async_call(DOMAIN, "release", {"entity_id": room}, blocking=True)
    assert hass.states.get(sensor).state == "1"
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, "button", "release_control")},
        blocking=True
    )
    assert hass.states.get(sensor).state == "0"
    assert entry.runtime_data.control.active == {}


async def test_an_override_the_pump_has_forgotten_leaves_the_count(hass):
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    sensor = _entity_id(hass, "sensor", "active_overrides")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    # The manager keeps its moments on the wall clock, which the test core's
    # timers do not move, so it is handed one the test moves itself.
    now = [1_000_000.0]
    entry.runtime_data.control._clock = lambda: now[0]
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    assert hass.states.get(sensor).state == "1"

    # The line goes: the keepalive writes fail, and once the last write that
    # reached the pump is five minutes old the override is given up.
    client.answers = False
    await _advance(hass, CONTROL_KEEPALIVE_SECONDS + 1)
    assert hass.states.get(sensor).state == "1", "pumpen håller värdet i fem minuter"
    now[0] += CONTROL_EXPIRY_SECONDS + 1
    await _advance(hass, CONTROL_KEEPALIVE_SECONDS + 1)
    state = hass.states.get(sensor)
    assert state.state == "0", "räknaren står inte kvar på ett glömt värde"
    assert state.attributes["overrides"] == []


async def test_the_sensor_stands_still_through_ten_minutes_of_keepalive(hass, freezer):
    """F6.2: the list carries when the value was set, so the recorder gets no row a minute.

    The keepalive writes 1010 every minute and the Modbus rounds write the
    sensor every 30 s. The list used to carry when a write last reached the
    pump, which moved with every keepalive, so the sensor got a state_changed
    a minute, a row in states and one in state_attributes, while its count
    stood still. The room setpoint's own attributes still move, as before.
    """
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    entry.runtime_data.control._clock = lambda: dt_util.utcnow().timestamp()
    sensor = _entity_id(hass, "sensor", "active_overrides")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    (held,) = hass.states.get(sensor).attributes["overrides"]
    set_at = held["set_at"]

    changed: dict[str, int] = {sensor: 0, room: 0}

    @callback
    def _note(event) -> None:
        entity_id = event.data["entity_id"]
        if entity_id in changed:
            changed[entity_id] += 1

    hass.bus.async_listen(EVENT_STATE_CHANGED, _note)
    for _step in range(20):
        freezer.tick(timedelta(seconds=30))
        async_fire_time_changed(hass)
        await _let_the_background_run(hass)

    assert client.writes.count((1010, 215)) == 11, "the set and ten keepalive writes"
    assert changed[sensor] == 0, "inga rader i recordern för en styrning som står still"
    assert changed[room] > 0, "the keepalive did run, and the control's own moment follows it"
    state = hass.states.get(sensor)
    assert state.state == "1"
    (held,) = state.attributes["overrides"]
    assert held["set_at"] == set_at

    # A change of value is news, once.
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21},
                                   blocking=True)
    assert changed[sensor] == 1
    (held,) = hass.states.get(sensor).attributes["overrides"]
    assert held["set_at"] > set_at and held["value"] == 21.0


async def test_an_override_set_between_two_ticks_is_given_up_at_its_expiry(hass, freezer):
    """F2.2: the release comes at ok_at plus five minutes, not at the next keepalive minute.

    The first set starts the keepalive's minute; the EMS sets a new value
    40 s into it, and the line goes 5 s later. The tick at five minutes finds
    the last write 260 s old and keeps it; the next tick is 20 s after the
    pump has let go. The check armed at the first miss releases it on time,
    and the count and control_active follow at once.
    """
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    entry.runtime_data.control._clock = lambda: dt_util.utcnow().timestamp()
    sensor = _entity_id(hass, "sensor", "active_overrides")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")

    async def tick(seconds: float) -> None:
        freezer.tick(timedelta(seconds=seconds))
        async_fire_time_changed(hass)
        await _let_the_background_run(hass)

    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    await tick(40)
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 20.5},
                                   blocking=True)
    ok_at = dt_util.utcnow()
    await tick(5)
    client.answers = False
    await tick(15)  # the keepalive's first miss, a minute after the first set
    for _minute in range(4):
        await tick(60)
    assert dt_util.utcnow() - ok_at == timedelta(seconds=260)
    assert hass.states.get(sensor).state == "1", "the tick at five minutes keeps it"
    await tick(39)
    assert hass.states.get(sensor).state == "1"
    assert entry.runtime_data.control.active == {1010: 205}
    # The number is available through the lost rounds (F2.1), so it says so too.
    assert hass.states.get(room).attributes["control_active"] is True
    await tick(1.001)  # ok_at plus 300 s, and the next tick is 20 s away
    assert hass.states.get(sensor).state == "0", "släppt när pumpen har glömt, inte vid nästa minut"
    assert entry.runtime_data.control.active == {}
    state = hass.states.get(room)
    assert state.attributes["control_active"] is False
    assert "valid_until" not in state.attributes


async def test_the_hot_water_mode_shows_the_pumps_program_and_holds_it_when_chosen(hass):
    await _set_up(hass)
    (client,) = FakeModbus.instances
    mode = _entity_id(hass, "select", "ctl_dhw_mode")
    state = hass.states.get(mode)
    assert state.state == "Normal", "pumpens eget program i vila"
    assert RELEASE in state.attributes["options"]
    assert state.attributes["control_active"] is False
    assert state.attributes["pump_value"] == state.attributes["pumpens värde"] == "Normal"

    # Choosing the mode the list already shows writes it and holds it.
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": mode, "option": "Normal"}, blocking=True)
    assert client.writes == [(1007, 1)]
    assert hass.states.get(mode).attributes["control_active"] is True

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": mode, "option": "Komfort"}, blocking=True)
    state = hass.states.get(mode)
    assert state.state == "Komfort"
    assert state.attributes["pump_value"] == "Normal", "pumpens program står bredvid"
    assert state.attributes["styrning aktiv"] == "ja"

    await hass.services.async_call("select", "select_option",
                                   {"entity_id": mode, "option": RELEASE}, blocking=True)
    state = hass.states.get(mode)
    assert state.state == "Normal"
    assert state.attributes["control_active"] is False
    assert client.writes == [(1007, 1), (1007, 2)], "släppet skriver inget"


async def test_a_number_carries_the_pumps_value_beside_its_own(hass):
    await _set_up(hass)
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    setpoint = _entity_id(hass, "number", "ctl_dhw_setpoint")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 22},
                                   blocking=True)
    state = hass.states.get(room)
    assert state.state == "22.0"
    assert state.attributes["pump_value"] == 20.5
    assert "pump_value" not in hass.states.get(setpoint).attributes


async def test_a_lost_round_keeps_the_controls_available_and_an_action_is_written(hass):
    """F2.1, F2.3: nothing an action sets in a lost round is dropped, and no release is shown.

    The numbers used to go unavailable with the round, so Home Assistant
    skipped a set_value with no more than a warning, and an energy manager
    that counted it as sent held off for an hour. The hot water mode showed
    Släpp styrningen and then Normal again, two changes nobody made.
    """
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    mode = _entity_id(hass, "select", "ctl_dhw_mode")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    sensor = _entity_id(hass, "sensor", "active_overrides")
    client.answers = False
    await _advance(hass, 31)
    assert not entry.runtime_data.modbus.last_update_success, "the round was lost"
    state = hass.states.get(room)
    assert state.state == "unknown", "talet är kvar men utan spegel i vila"
    assert state.attributes["control_active"] is False
    state = hass.states.get(mode)
    assert state.state == "unknown", "ett oläst 61500 är okänt, inget släpp"
    assert state.attributes["pump_value"] is None
    assert hass.states.get(sensor).state == "0"

    # The line answers writes again before the next round: set_value is written.
    client.answers = True
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 20},
                                   blocking=True)
    assert client.writes == [(1010, 200)]
    state = hass.states.get(room)
    assert state.state == "20.0" and state.attributes["control_active"] is True
    assert "last_written" in state.attributes and "valid_until" in state.attributes
    assert hass.states.get(sensor).state == "1"

    # And back: the pump's own program again, and the held value beside its own.
    await _advance(hass, 31)
    assert hass.states.get(mode).state == "Normal"
    state = hass.states.get(room)
    assert state.state == "20.0" and state.attributes["pump_value"] == 20.5


async def test_a_held_number_keeps_its_attributes_through_a_lost_round(hass):
    await _set_up(hass)
    (client,) = FakeModbus.instances
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 22},
                                   blocking=True)
    client.answers = False
    await _advance(hass, 31)
    state = hass.states.get(room)
    assert state.state == "22.0"
    assert state.attributes["control_active"] is True and state.attributes["styrning aktiv"] == "ja"
    assert "valid_until" in state.attributes
    assert state.attributes["pump_value"] is None, "pumpens värde är inte läst i det varvet"
