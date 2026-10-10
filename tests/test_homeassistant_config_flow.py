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
    FakeModbus,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_menu import VSH  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import device_registry as dr, issue_registry as ir  # noqa: E402
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_DISPLAY,
    CONF_MENU,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
)
from custom_components.ctc_ecozenith.discovery import DiscoveredDisplay, WebProbe  # noqa: E402
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

FLOW = f"custom_components.{DOMAIN}.config_flow"

#: Documentation addresses (RFC 5737), never a house's.
NETWORK = ipaddress.ip_network("192.0.2.0/24")
FOUND = DiscoveredDisplay(host="192.0.2.55", settings_name="settings_ezi2xx.bin")
OTHER = DiscoveredDisplay(host="192.0.2.60", settings_name="settings_ezi3xx.bin")

ADDRESS = {CONF_HOST: FOUND.host, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1}


@pytest.fixture
def display():
    """The display's answer, typed or discovered, and a menu that reads as nothing."""
    probe = AsyncMock(return_value=FOUND)
    with (
        patch(f"{FLOW}.async_probe_host", probe),
        patch(f"{FLOW}.async_probe_web", AsyncMock(return_value=WebProbe(FOUND, answered=True))),
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
    # The family part of the settings file, kept for the report (R19).
    assert result["data"]["settings_stem"] == "ezi2xx"
    assert result["data"][CONF_DISPLAY] is True
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


# ------------------------------------------------------- Modbus alone (R11)


async def _type_the_address(hass, web: WebProbe):
    """The address form, filled in, with the web port answering as told."""
    with patch(f"{FLOW}.async_probe_web", AsyncMock(return_value=web)):
        result = await _start(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manual"}
        )
        return await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)


async def test_a_typed_address_whose_web_is_silent_is_added_on_modbus_alone(hass, stubs):
    discover_pages = AsyncMock(return_value=MenuReading())
    with (
        patch(f"{FLOW}.async_discover_pages", discover_pages),
        patch(
            f"custom_components.{DOMAIN}.async_read_identity",
            AsyncMock(return_value=Identity()),
        ) as identity,
    ):
        result = await _type_the_address(hass, WebProbe(None, answered=False))
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == f"EcoZenith ({FOUND.host})"
        assert result["data"][CONF_DISPLAY] is False
        assert result["data"]["model"] == "EcoZenith"
        assert "settings_stem" not in result["data"], "ingen inställningsfil, ingen stam"
        assert result["options"][CONF_SLOW_PAGES] == []
        assert CONF_MENU not in result["options"]
        discover_pages.assert_not_awaited()

        await hass.async_block_till_done()
        await _let_the_background_run(hass)
        entry = result["result"]
        assert entry.state is ConfigEntryState.LOADED
        # Nothing that needs the display runs: no menu, no identity.
        stubs.discover.assert_not_awaited()
        identity.assert_not_awaited()

    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.name == "CTC EcoZenith"
    assert device.configuration_url is None
    issues = ir.async_get(hass)
    for key in ("pages_missing", "history_page_missing", "identity_incomplete"):
        assert issues.async_get_issue(DOMAIN, f"{entry.entry_id}_{key}") is None, key


async def test_a_silent_web_port_and_a_silent_modbus_is_no_ctc(hass, stubs):
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _type_the_address(hass, WebProbe(None, answered=False))
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "not_a_ctc"}


async def test_something_else_on_the_web_port_is_no_ctc_and_modbus_is_not_asked(hass, stubs):
    result = await _type_the_address(hass, WebProbe(None, answered=True))
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "not_a_ctc"}
    assert FakeModbus.instances == []


async def test_read_again_that_finds_pages_makes_it_an_entry_with_a_display(hass, stubs):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{FOUND.host}",
        title=f"EcoZenith ({FOUND.host})",
        data={**ADDRESS, "model": "EcoZenith", "settings_name": "", CONF_DISPLAY: False},
        options={CONF_SLOW_PAGES: []},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    with (
        patch(
            f"{FLOW}.async_rescan_pages",
            AsyncMock(return_value=MenuReading(pages=VSH, complete=True)),
        ),
        patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=FOUND)),
    ):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True}
        )
        assert result["step_id"] == "rescan"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_SLOW_PAGES: ["20", "25"]}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.data[CONF_DISPLAY] is True
    assert entry.data["model"] == "EcoZenith i255"
    assert entry.data["settings_stem"] == "ezi2xx"
    assert entry.state is ConfigEntryState.LOADED
    # Data and options went in one write: one reload, so two clients in all.
    assert len(FakeModbus.instances) == 2


# ------------------------------------------------- a Modbus place taken (L12)


async def _pick_the_found_unit(hass):
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "scan"}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"picked": FOUND.host}
    )


async def test_a_taken_modbus_place_has_a_step_of_its_own(hass, stubs, sweep, display, monkeypatch):
    """Accepted, then dropped at the first request: another client holds the place.

    The probe runs once the flow's own client is closed, the step names what
    usually holds the place, and pressing it tries Modbus again.
    """
    seen: list[list[int]] = []

    async def probe(host, port, unit):
        # The flow's own client, closed before the probe knocks.
        seen.append([client.closes for client in FakeModbus.instances])
        return "busy"

    stubs.modbus_probe.side_effect = probe
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
        assert result["step_id"] == "modbus_busy"
        assert result["data_schema"] is None or not result["data_schema"].schema
        assert result["description_placeholders"]["host"] == FOUND.host
        assert not result["errors"]
        assert seen == [[1]]

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["step_id"] == "modbus_busy"
        assert result["errors"] == {"base": "modbus_busy"}

        monkeypatch.setattr(DeadModbus, "answers", True)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()


async def test_an_answer_a_moment_later_is_called_a_hiccup(hass, stubs, sweep, display):
    stubs.modbus_probe.return_value = "answered"
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "modbus_transient"}


async def test_a_closed_port_is_the_step_about_turning_modbus_on(hass, stubs, sweep, display):
    stubs.modbus_probe.return_value = "closed"
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
    assert result["step_id"] == "modbus_failed"


async def test_a_probe_that_breaks_leaves_the_flow_standing(hass, stubs, sweep, display):
    stubs.modbus_probe.side_effect = RuntimeError("no socket for you")
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "modbus_failed"


async def test_an_answer_that_is_not_the_register_points_at_the_port(hass, stubs, sweep, display):
    """Something speaks on the port, and not as the heat pump: no hiccup, no try again.

    A Modbus exception or a web server on the port typed for Modbus used to
    count as an answer, and the form said it was a passing hiccup at every
    try. It goes back to the address form, which names the port and the
    Modbus address, whatever form the address came from.
    """
    stubs.modbus_probe.return_value = "rejected"
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
        assert result["step_id"] == "manual"
        assert result["errors"] == {"base": "modbus_rejected"}
        hass.config_entries.flow.async_abort(result["flow_id"])

        # Typed, with the display silent this time: still the port.
        result = await _type_the_address(hass, WebProbe(None, answered=False))
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": "modbus_rejected"}


# ------------------- the step that says the display answers, only when it does


async def _from_the_modbus_step(hass, web: WebProbe):
    """Modbus off at a found unit, then another address typed into the step about it."""
    with patch(f"{FLOW}.CtcModbusClient", DeadModbus):
        result = await _pick_the_found_unit(hass)
        assert result["step_id"] == "modbus_failed"
        with patch(f"{FLOW}.async_probe_web", AsyncMock(return_value=web)):
            return await hass.config_entries.flow.async_configure(
                result["flow_id"], {**ADDRESS, CONF_HOST: OTHER.host}
            )


@pytest.mark.parametrize(
    ("web", "verdict", "error"),
    [
        (WebProbe(None, answered=True), "closed", "not_a_ctc"),
        (WebProbe(None, answered=False), "closed", "not_a_ctc"),
        (WebProbe(None, answered=False), "silent", "not_a_ctc"),
        (WebProbe(None, answered=False), "answered", "modbus_transient"),
    ],
    ids=["other-device-on-80", "nothing-answers", "silent", "modbus-alone-hiccup"],
)
async def test_a_new_address_without_a_display_leaves_the_modbus_step(
    hass, stubs, sweep, display, web, verdict, error
):
    """The step modbus_failed says the display at {host} answers.

    Typed into it, an address where no display answers used to come back on
    the same step, with the new address in that sentence and an error beside
    it saying the address does not answer as a CTC display. It goes to the
    address form instead.
    """
    verdicts = iter(["closed", verdict])
    stubs.modbus_probe.side_effect = lambda *args: next(verdicts)
    result = await _from_the_modbus_step(hass, web)
    assert result["step_id"] == "manual"
    assert result["errors"] == {"base": error}
    assert result["description_placeholders"]["host"] == OTHER.host


async def test_a_new_address_whose_display_answers_stays_on_the_modbus_step(
    hass, stubs, sweep, display
):
    stubs.modbus_probe.return_value = "closed"
    other = WebProbe(OTHER, answered=True)
    result = await _from_the_modbus_step(hass, other)
    assert result["step_id"] == "modbus_failed"
    assert result["errors"] == {"base": "modbus_failed"}
    assert result["description_placeholders"]["host"] == OTHER.host
