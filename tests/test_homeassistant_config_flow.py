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
    DeadModbus,
    _needs_auto_asyncio_mode,
    stubs,
)

from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)
from custom_components.ctc_ecozenith.discovery import DiscoveredDisplay  # noqa: E402

FLOW = f"custom_components.{DOMAIN}.config_flow"

#: Documentation addresses (RFC 5737), never a house's.
NETWORK = ipaddress.ip_network("192.0.2.0/24")
FOUND = DiscoveredDisplay(host="192.0.2.55", settings_name="settings_ezi2xx.bin")
OTHER = DiscoveredDisplay(host="192.0.2.60", settings_name="settings_ezi3xx.bin")

ADDRESS = {CONF_HOST: FOUND.host, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1}


@pytest.fixture
def display():
    """The display's answer to a typed address, and a menu that reads as nothing."""
    probe = AsyncMock(return_value=FOUND)
    with (
        patch(f"{FLOW}.async_probe_host", probe),
        patch(f"{FLOW}.async_discover_pages", AsyncMock(return_value=MenuReading())),
    ):
        yield probe


def _listed(result) -> list[str]:
    """The values offered in the list of found units."""
    (field,) = result["data_schema"].schema.values()
    return [option["value"] for option in field.config["options"]]


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


# ------------------------------------------------ the flow for a stranger (R16)


async def test_modbus_that_does_not_answer_has_a_step_of_its_own(
    hass, stubs, sweep, display, monkeypatch
):
    """The most common first-time failure: the display answers, Modbus does not.

    It used to land on the address form saying no CTC had been found, right
    after one had been. The step of its own names the menu on the panel, takes
    the same fields, says so when a try from it fails again, and goes on once
    Modbus answers.
    """
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "scan"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"picked": FOUND.host}
        )
        assert result["step_id"] == "modbus_failed"
        assert not result["errors"]
        assert result["description_placeholders"]["host"] == FOUND.host

        result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
        assert result["step_id"] == "modbus_failed"
        assert result["errors"] == {"base": "modbus_failed"}

        monkeypatch.setattr(DeadModbus, "answers", True)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"EcoZenith i255 ({FOUND.host})"
    await hass.async_block_till_done()


async def test_an_address_already_set_up_is_left_out_of_the_list(hass, stubs, sweep):
    _networks, discover = sweep
    MockConfigEntry(
        domain=DOMAIN, unique_id=f"{DOMAIN}_{FOUND.host}", data={CONF_HOST: FOUND.host}
    ).add_to_hass(hass)
    discover.return_value = [FOUND, OTHER]
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    assert result["step_id"] == "scan"
    assert _listed(result) == [OTHER.host, "manual"]
    assert result["description_placeholders"]["count"] == "1"

    # And with nothing new on the network, the address form says so.
    discover.return_value = [FOUND]
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "nothing_found"}


async def test_a_discovered_unit_carries_its_model_and_address_on_the_card(hass, stubs, display):
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "dhcp"},
        data=DhcpServiceInfo(ip=FOUND.host, hostname="", macaddress="020000000001"),
    )
    assert result["step_id"] == "confirm"
    assert "privacy_url" in result["description_placeholders"]
    (flow,) = hass.config_entries.flow.async_progress()
    assert flow["context"]["title_placeholders"] == {"name": f"EcoZenith i255 ({FOUND.host})"}
