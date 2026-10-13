"""Extra hot water under a real Home Assistant core (roadmap R50).

The number for 1006 stands empty at rest whatever 61503 reads, carries no
pump_value held or at rest, is written and kept alive every minute as before,
and the sensor Aktiva överstyrningar lists the hold and nothing of 61503. The
stored register keeps its own sensor. What can be read without a core is in
test_extra_hot_water.py.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_extra_hot_water.py
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    READINGS,
    FakeModbus,
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)

from custom_components.ctc_ecozenith.const import CONTROL_KEEPALIVE_SECONDS, DOMAIN


@pytest.fixture(autouse=True)
def _time_left(monkeypatch):
    """61503 answers one and a half hours, raw 3, as if a start at the panel ran."""
    monkeypatch.setitem(READINGS, 61503, 3)


async def test_at_rest_the_number_is_empty_and_the_stored_register_keeps_its_sensor(hass):
    await _set_up(hass)
    number = _entity_id(hass, "number", "ctl_extra_dhw")
    state = hass.states.get(number)
    assert state.state == "unknown", "inget påstående om tiden som är kvar"
    assert "pump_value" not in state.attributes and "pumpens värde" not in state.attributes
    assert state.attributes["control_active"] is False
    stored = hass.states.get(_entity_id(hass, "sensor", "set_extra_dhw"))
    assert stored.state == "1.5"


async def test_a_start_is_written_kept_alive_and_listed_without_61503(hass):
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    number = _entity_id(hass, "number", "ctl_extra_dhw")
    sensor = _entity_id(hass, "sensor", "active_overrides")

    await hass.services.async_call("number", "set_value", {"entity_id": number, "value": 1},
                                   blocking=True)
    assert client.writes == [(1006, 2)]
    state = hass.states.get(number)
    assert state.state == "1.0"
    assert state.attributes["control_active"] is True
    assert "pump_value" not in state.attributes and "pumpens värde" not in state.attributes

    (held,) = hass.states.get(sensor).attributes["overrides"]
    assert {key: held[key] for key in ("name", "register", "value")} == {
        "name": "Styr extra varmvatten", "register": "1006", "value": 1.0,
    }
    assert set(held) == {"name", "register", "value", "set_at"}
    assert "61503" not in json.dumps(hass.states.get(sensor).attributes, ensure_ascii=False)

    # The keepalive writes it again a minute later, as for every control.
    await _advance(hass, CONTROL_KEEPALIVE_SECONDS + 1)
    assert client.writes.count((1006, 2)) == 2
    assert entry.runtime_data.control.active == {1006: 2}
    assert "pump_value" not in hass.states.get(number).attributes

    # Let go of, it stands empty again rather than on 61503's 1.5.
    await hass.services.async_call(DOMAIN, "release", {"entity_id": number}, blocking=True)
    state = hass.states.get(number)
    assert state.state == "unknown"
    assert state.attributes["control_active"] is False
    assert hass.states.get(sensor).state == "0"
