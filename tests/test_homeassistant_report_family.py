"""The family flag from a real entry to the report, under a real Home Assistant core (R19).

An entry for a family the integration did not know, as the config flow has
always written it, sends family_<stem> beside models ["other"], loaded or
still waiting for the controller; the houses' known models send none. Shares
the stand-ins and fixtures of test_homeassistant.py and runs the same way,
from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_report_family.py
"""

from __future__ import annotations

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
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)


async def _set_up_unknown(hass) -> MockConfigEntry:
    """An entry for a family nobody knew, named the way the config flow names it."""
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"CTC (ezi4xx) ({HOST})",
        data={
            CONF_HOST: HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": "CTC (ezi4xx)",
            "settings_name": "settings_ezi4xx.bin",
        },
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_an_unknown_family_is_named_in_the_report(hass, stubs):
    entry = await _set_up_unknown(hass)
    assert entry.state is ConfigEntryState.LOADED
    report = integration._stats_extra_for(hass, entry)
    assert report["models"][0] == "other"
    assert report["features"]["family_ezi4xx"] is True


async def test_an_unknown_family_waiting_for_the_controller_is_named_too(hass, stubs):
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up_unknown(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    report = integration._stats_extra_for(hass, entry)
    assert report["models"] == ["other"]
    assert report["features"]["family_ezi4xx"] is True


async def test_a_known_model_sends_no_family(hass, stubs):
    entry = await _set_up(hass)
    features = integration._stats_extra_for(hass, entry)["features"]
    assert not [key for key in features if key.startswith("family_")]
