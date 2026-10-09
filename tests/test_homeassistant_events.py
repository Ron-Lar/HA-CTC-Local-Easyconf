"""The event entity through a lost Modbus round, under a real Home Assistant core (F4.2).

The entity's state is the moment of its last event. With the coordinator's
availability one failed round took it to unavailable and the next round wrote
the old event back, and a state trigger, the plain kind the README recommends,
fired the same alarm a second time. Now the entity stands: the Modbus sensors
go unavailable and say that the line is down, the event entity keeps its last
event, and the recovery writes nothing, so there is no state change for a
trigger to fire on until the next real transition.

Shares the stand-ins and fixtures of test_homeassistant.py and the helpers of
test_homeassistant_transitions.py, and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_events.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_transitions import HEATING, READY, _controller, _status  # noqa: E402

from homeassistant.const import EVENT_STATE_CHANGED  # noqa: E402
from homeassistant.core import callback  # noqa: E402


async def test_the_events_stand_through_a_lost_round_and_do_not_fire_again(hass, stubs):
    await _set_up(hass)
    events = _entity_id(hass, "event", "events")
    outdoor = _entity_id(hass, "sensor", "outdoor_temp")
    changes: list[tuple[str, str | None] | None] = []

    @callback
    def _note(event) -> None:
        if event.data["entity_id"] != events:
            return
        new = event.data["new_state"]
        changes.append((new.state, new.attributes.get("event_type")) if new else None)

    hass.bus.async_listen(EVENT_STATE_CHANGED, _note)

    await _status(hass, HEATING)
    started = hass.states.get(events)
    assert started.attributes["event_type"] == "kompressor_start"
    assert changes == [(started.state, "kompressor_start")]

    # The line is lost for a round: the Modbus sensors say so, the event stands.
    _controller().answers = False
    await _advance(hass, 31)
    assert hass.states.get(outdoor).state == "unavailable"
    kept = hass.states.get(events)
    assert kept.state == started.state
    assert kept.attributes["event_type"] == "kompressor_start"
    assert changes == [(started.state, "kompressor_start")], "ingen övergång till otillgänglig"

    # And back: nothing is written again, so a state trigger has nothing to
    # fire on; the sensors are back as well.
    _controller().answers = True
    await _advance(hass, 31)
    assert hass.states.get(outdoor).state == "7.2"
    assert hass.states.get(events).state == started.state
    assert changes == [(started.state, "kompressor_start")], "återkomsten är ingen händelse"

    # The next real transition fires as before, once.
    await _status(hass, READY)
    assert len(changes) == 2
    assert changes[-1][1] == "kompressor_stopp"
    assert hass.states.get(events).attributes["event_type"] == "kompressor_stopp"
