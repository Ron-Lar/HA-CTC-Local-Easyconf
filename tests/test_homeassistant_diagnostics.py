"""The diagnostics download under a real Home Assistant core (R55).

test_diagnostics.py proves the build and the wash on stand-ins, with a copy of
Home Assistant's redaction. Here the shell itself runs: the real
async_redact_data, the real entry with its title and unique id, the real
device registry's identifiers, for an entry that is loaded and for one still
waiting for the controller, which is when a download is wanted most.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_diagnostics.py
"""

from __future__ import annotations

import json
import re
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    DeadModbus,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402

from custom_components.ctc_ecozenith import diagnostics  # noqa: E402
from custom_components.ctc_ecozenith.const import DOMAIN  # noqa: E402

SEQUENCE = re.compile(r"(?<!\d)0001(?!\d)")


def _names_nothing(found: dict) -> str:
    text = json.dumps(found, ensure_ascii=False)
    assert HOST not in text
    assert IDENTITY["mac"] not in text
    assert IDENTITY["serial"] not in text
    assert not SEQUENCE.search(text), "löpnumret står kvar"
    return text


async def test_a_loaded_entry_downloads_without_its_address_or_its_machine(hass, stubs):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED

    found = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    _names_nothing(found)
    assert found["entry"]["title"] == "**REDACTED**"
    assert found["entry"]["data"]["host"] == "**REDACTED**"
    assert found["device"]["identifiers"] == "**REDACTED**"
    assert found["identity"]["product"] == "7331"
    assert found["identity"]["made"] == "2412"
    assert found["entry"]["loaded"] is True
    assert found["control"]["enabled"] is True
    assert found["modbus"]["block_plan"], "blockplanen syns"
    assert found["menu"]["readings_this_run"] == 0


async def test_an_entry_waiting_for_the_controller_downloads_too(hass, stubs):
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY

    found = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
    _names_nothing(found)
    assert found["entry"]["loaded"] is False
    assert found["identity"]["product"] == "7331"
    assert found["modbus"] is None
