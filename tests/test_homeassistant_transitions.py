"""The transition sensors under a real Home Assistant core (R27, R31).

The watch itself is tested without Home Assistant in test_transitions.py. What
only a real core can show is the wiring: that the first round of a set-up is
the baseline and raises nothing, that the sensors read the watch after each
Modbus round, and that Home Assistant accepts what they offer it: an aware
timestamp, a total with a reset and a duration in minutes.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_transitions.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    FakeModbus,
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components.ctc_ecozenith.const import DOMAIN  # noqa: E402

#: The heat pump's status register and the codes the test drives it through.
HP_STATUS = 62017
READY, HEATING, DEFROST, BLOCKED = 1, 3, 4, 6


def _controller() -> FakeModbus:
    return FakeModbus.instances[-1]


async def _status(hass, code: int) -> None:
    """Have the controller answer this status and let the next round read it."""
    _controller().registers[HP_STATUS] = code
    await _advance(hass, 31)


async def test_the_first_round_is_the_baseline_and_a_start_is_counted(hass, stubs):
    await _set_up(hass)
    last_start = _entity_id(hass, "sensor", "last_start")
    starts = _entity_id(hass, "sensor", "starts_today")
    last_run = _entity_id(hass, "sensor", "last_run")

    # The pump was found idle: nothing has happened yet, and the count says
    # since when it counts.
    assert hass.states.get(last_start).state == "unknown"
    before = hass.states.get(starts)
    assert before.state == "0"
    assert before.attributes["state_class"] == "total"
    assert before.attributes["last_reset"]
    assert before.attributes["räknas sedan"]
    assert hass.states.get(last_run).state == "unknown"

    # The compressor starts: one start, dated, with the weather at the time.
    await _status(hass, HEATING)
    assert hass.states.get(starts).state == "1"
    started = hass.states.get(last_start)
    assert started.state not in ("unknown", "unavailable")
    assert started.attributes["device_class"] == "timestamp"
    assert started.attributes["systemstatus vid starten"] == "Värmepump övre"
    assert started.attributes["utetemperatur vid starten"] == 7.2
    assert started.attributes["körning pågår"] is True

    # A defrost is part of the run, and counted and dated on its own (R31).
    defrosts = _entity_id(hass, "sensor", "defrosts_today")
    last_defrost = _entity_id(hass, "sensor", "last_defrost")
    assert hass.states.get(defrosts).state == "0"
    assert hass.states.get(last_defrost).state == "unknown"
    await _status(hass, DEFROST)
    assert hass.states.get(starts).state == "1"
    assert hass.states.get(defrosts).state == "1"
    thawing = hass.states.get(last_defrost)
    assert thawing.attributes["device_class"] == "timestamp"
    assert thawing.attributes["pågår"] is True
    assert thawing.attributes["längd"] is None
    assert thawing.attributes["utetemperatur vid starten"] == 7.2
    await _status(hass, HEATING)
    thawed = hass.states.get(last_defrost)
    assert thawed.state == thawing.state
    assert thawed.attributes["pågår"] is False
    assert thawed.attributes["längd"] >= 0
    assert thawed.attributes["avslutad"]
    # The stop after it ends the run.
    await _status(hass, READY)
    run = hass.states.get(last_run)
    assert float(run.state) >= 0
    assert run.attributes["unit_of_measurement"] == "min"
    assert run.attributes["device_class"] == "duration"
    assert run.attributes["startade"] and run.attributes["slutade"]
    assert hass.states.get(last_start).attributes["körning pågår"] is False

    # A second start the same day.
    await _status(hass, HEATING)
    assert hass.states.get(starts).state == "2"


async def test_the_mean_run_needs_the_history_page(hass, stubs):
    # No display page is harvested in this set-up, so there is no starts per
    # day row to divide by, and the sensor is not made at all.
    await _set_up(hass)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_mean_run_24h") is None
    # The five the watch alone can give are there.
    for key in ("last_start", "starts_today", "last_run", "defrosts_today", "last_defrost"):
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_{key}"), key
