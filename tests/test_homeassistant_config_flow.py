"""The set-up flow under a real Home Assistant core, as a stranger meets it.

The ordinary suite can only read the flow as source, since config_flow.py
imports Home Assistant and voluptuous at the top. Here the flow is driven from
the first screen: nothing goes on the network before somebody has chosen to
search (R70), and only Home Assistant's own networks are searched (L8).

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_config_flow.py
"""

from __future__ import annotations

import ipaddress
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _needs_auto_asyncio_mode,
    stubs,
)

from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.ctc_ecozenith.const import DOMAIN  # noqa: E402
from custom_components.ctc_ecozenith.discovery import DiscoveredDisplay  # noqa: E402

FLOW = f"custom_components.{DOMAIN}.config_flow"

#: Documentation addresses (RFC 5737), never a house's.
NETWORK = ipaddress.ip_network("192.0.2.0/24")
FOUND = DiscoveredDisplay(host="192.0.2.55", settings_name="settings_ezi2xx.bin")


@pytest.fixture
def sweep():
    """The two calls a search makes, stood in for, so a test can see whether they ran."""
    networks = AsyncMock(return_value=[NETWORK])
    discover = AsyncMock(return_value=[FOUND])
    with (
        patch(f"{FLOW}.async_home_assistant_networks", networks),
        patch(f"{FLOW}.async_discover", discover),
    ):
        yield networks, discover


async def _start(hass):
    return await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})


# ------------------------------------------------- nothing before a choice (R70)


async def test_the_flow_asks_before_anything_goes_on_the_network(hass, stubs, sweep):
    networks, discover = sweep
    result = await _start(hass)
    assert result["type"] is FlowResultType.MENU
    assert list(result["menu_options"]) == ["scan", "manual"]
    # The sentence on what is switched on from the start links the privacy page.
    assert "privacy_url" in result["description_placeholders"]
    networks.assert_not_awaited()
    discover.assert_not_awaited()

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    discover.assert_awaited_once()
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "scan"
    assert result["description_placeholders"]["count"] == "1"


async def test_entering_an_address_never_sweeps(hass, stubs, sweep):
    networks, discover = sweep
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "manual"
    assert not result["errors"]
    networks.assert_not_awaited()
    discover.assert_not_awaited()


async def test_a_search_that_finds_nothing_says_so_on_the_address_form(hass, stubs, sweep):
    _networks, discover = sweep
    discover.return_value = []
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "nothing_found"}


# ------------------------------------------- Home Assistant's networks only (L8)


async def test_without_an_adapter_the_flow_goes_to_the_address_form(hass, stubs, sweep):
    networks, discover = sweep
    networks.return_value = []
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "nothing_found"}
    networks.assert_awaited_once()
    discover.assert_not_awaited()
