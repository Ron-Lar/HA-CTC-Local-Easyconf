"""A taken Modbus place gets a name at set-up, under a real Home Assistant core (L12).

The controller takes one Modbus client at a time. With a modbus: block left
in configuration.yaml, the entry sat in setup_retry saying "no Modbus register
could be read", the same words as for a pump that is switched off. Now the
set-up probes the port once its own client is shut, raises ConfigEntryNotReady
with the cause, and says a taken place in the repairs view until the first
reading that works.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_modbus_busy.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    DeadModbus,
    FakeModbus,
    _advance,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.ctc_ecozenith.const import DOMAIN  # noqa: E402


def _busy_notice(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_modbus_busy")


async def test_a_taken_place_is_named_and_the_notice_goes_at_the_first_reading(
    hass, stubs, monkeypatch
):
    asked: list[tuple] = []

    async def probe(host, port, unit):
        # Never beside a session: this attempt's client is shut by now.
        asked.append((host, port, unit, [client.shut_down for client in FakeModbus.instances]))
        return "busy"

    stubs.modbus_probe.side_effect = probe
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        assert entry.error_reason_translation_key == "modbus_busy"
        assert entry.reason == "Another client holds the heat pump's only Modbus connection"
        assert asked == [(HOST, 502, 1, [True])]
        notice = _busy_notice(hass, entry)
        assert notice is not None and notice.translation_key == "modbus_busy"

        # The other client lets go, and Home Assistant's own retry finds the pump.
        monkeypatch.setattr(DeadModbus, "answers", True)
        await _advance(hass, 61)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert _busy_notice(hass, entry) is None


async def test_a_closed_port_is_named_without_a_notice(hass, stubs):
    stubs.modbus_probe.return_value = "closed"
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key == "modbus_closed"
    assert _busy_notice(hass, entry) is None


async def test_a_failure_the_probe_cannot_name_is_raised_as_it_was(hass, stubs):
    # "silent", the fixture's own answer: the reason Home Assistant shows is
    # the coordinator's, as before the probe.
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert entry.error_reason_translation_key != "modbus_busy"
    assert _busy_notice(hass, entry) is None
    stubs.modbus_probe.assert_awaited_once()


async def test_a_probe_that_breaks_hides_nothing(hass, stubs):
    stubs.modbus_probe.side_effect = RuntimeError("no socket for you")
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_set_up_that_works_never_probes(hass, stubs):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    stubs.modbus_probe.assert_not_awaited()
