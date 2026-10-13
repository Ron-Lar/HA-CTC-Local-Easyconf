"""What Home Assistant holds, under a real Home Assistant core (roadmap R49).

The sensor Aktiva överstyrningar follows every write, release and override
given up; the hot water mode shows the pump's own program from 61500 while
idle, carries it beside a held mode, and still writes a mode the list already
shows; and a lost Modbus round leaves the select available, so an action that
picks a mode is never dropped for it. What can be read without a core is in
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

from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import async_fire_time_changed  # noqa: E402
from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    READINGS,
    FakeModbus,
    _advance,
    _entity_id,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONTROL_EXPIRY_SECONDS,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
)
from custom_components.ctc_ecozenith.select import RELEASE  # noqa: E402


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

    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5}, blocking=True)
    state = hass.states.get(sensor)
    assert state.state == "1"
    (held,) = state.attributes["overrides"]
    assert held["name"] == "Styr rumsbörvärde" and held["register"] == "1010" and held["value"] == 21.5
    assert held["last_written"] == hass.states.get(room).attributes["last_written"]

    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": "Komfort"}, blocking=True)
    state = hass.states.get(sensor)
    assert state.state == "2"
    assert [item["namn"] for item in state.attributes["styrningar"]] == [
        "Styr rumsbörvärde", "Styr varmvattenläge",
    ]
    assert state.attributes["styrningar"][1]["värde"] == "Komfort"

    await hass.services.async_call(DOMAIN, "release", {"entity_id": room}, blocking=True)
    assert hass.states.get(sensor).state == "1"
    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, "button", "release_control")}, blocking=True
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
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5}, blocking=True)
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

    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5}, blocking=True)
    await tick(40)
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 20.5}, blocking=True)
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
    await tick(1.001)  # ok_at plus 300 s, and the next tick is 20 s away
    assert hass.states.get(sensor).state == "0", "släppt när pumpen har glömt, inte vid nästa minut"
    assert entry.runtime_data.control.active == {}


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
    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": "Normal"}, blocking=True)
    assert client.writes == [(1007, 1)]
    assert hass.states.get(mode).attributes["control_active"] is True

    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": "Komfort"}, blocking=True)
    state = hass.states.get(mode)
    assert state.state == "Komfort"
    assert state.attributes["pump_value"] == "Normal", "pumpens program står bredvid"
    assert state.attributes["styrning aktiv"] == "ja"

    await hass.services.async_call("select", "select_option", {"entity_id": mode, "option": RELEASE}, blocking=True)
    state = hass.states.get(mode)
    assert state.state == "Normal"
    assert state.attributes["control_active"] is False
    assert client.writes == [(1007, 1), (1007, 2)], "släppet skriver inget"


async def test_a_number_carries_the_pumps_value_beside_its_own(hass):
    await _set_up(hass)
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    setpoint = _entity_id(hass, "number", "ctl_dhw_setpoint")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 22}, blocking=True)
    state = hass.states.get(room)
    assert state.state == "22.0"
    assert state.attributes["pump_value"] == 20.5
    assert "pump_value" not in hass.states.get(setpoint).attributes


async def test_a_lost_round_keeps_the_select_available_while_the_numbers_go(hass):
    await _set_up(hass)
    (client,) = FakeModbus.instances
    mode = _entity_id(hass, "select", "ctl_dhw_mode")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    sensor = _entity_id(hass, "sensor", "active_overrides")
    client.answers = False
    await _advance(hass, 31)
    assert hass.states.get(room).state == "unavailable"
    state = hass.states.get(mode)
    assert state.state == RELEASE, "ingen spegel att lita på efter ett tappat varv"
    assert state.attributes["pump_value"] is None
    assert hass.states.get(sensor).state == "0"
    # And back: the pump's own program again.
    client.answers = True
    await _advance(hass, 31)
    assert hass.states.get(mode).state == "Normal"
    assert hass.states.get(room).state == "20.5"
