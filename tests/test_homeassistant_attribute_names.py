"""The English attribute names beside the Swedish ones, under a real core (R22).

What an automation actually reads is the state object, so the twins are read
off it here: a control's control_active, last_written and valid_until through
a write and a release, and a display row's source, page, screen and read_at,
the latest alarm's code and the binary's episodes after a harvest.

Shares the stand-ins and fixtures of test_homeassistant.py and
test_homeassistant_rows_alarms.py and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_attribute_names.py
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
from test_homeassistant_rows_alarms import (  # noqa: E402
    E017,
    ServingPanel,
    _row_entity,
    _set_up_with_the_page,
)

from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402


async def test_a_control_says_it_is_in_force_under_both_names(hass):
    await _set_up(hass)
    number = _entity_id(hass, "number", "ctl_room_setpoint_1")
    idle = hass.states.get(number).attributes
    assert idle["styrning aktiv"] == "nej" and idle["control_active"] is False
    assert "last_written" not in idle

    await hass.services.async_call(
        "number", "set_value", {"entity_id": number, "value": 21.5}, blocking=True
    )
    busy = hass.states.get(number).attributes
    assert busy["styrning aktiv"] == "ja" and busy["control_active"] is True
    assert busy["last_written"] == busy["senast skriven"]
    assert busy["valid_until"] == busy["gäller till"]

    await hass.services.async_call(
        "button", "press", {"entity_id": _entity_id(hass, "button", "release_control")},
        blocking=True,
    )
    released = hass.states.get(number).attributes
    assert released["control_active"] is False and "valid_until" not in released


async def test_a_display_row_and_the_alarm_carry_the_english_names(hass, stubs):
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = [E017]
    await _set_up_with_the_page(hass)
    await _advance(hass, MIN_FIRST_DELAY + 1)

    row = hass.states.get(_row_entity(hass, "utetemperatur")).attributes
    for swedish, english in (("källa", "source"), ("sida", "page"), ("skärm", "screen"), ("senast läst", "read_at")):
        assert row[english] == row[swedish], swedish

    last = hass.states.get(_entity_id(hass, "sensor", "last_alarm")).attributes
    assert last["code"] == last["kod"] == "E017"

    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm")).attributes
    (episode,) = alarm["episodes"]
    assert episode["code"] == "E017" and episode["end"] is None
    assert episode["outdoor_temperature"] == 7.2
    assert alarm["episoder"][0]["kod"] == "E017", "det svenska namnet står kvar en version"
