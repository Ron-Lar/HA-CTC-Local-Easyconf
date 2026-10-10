"""The device and its entities follow the heat pump to a new address (R20, R17).

Under a real Home Assistant core: an entry from before the device key keeps
every unique_id it had and gets the display's MAC on its device; the DHCP flow
recognises the unit by that MAC at a new address and moves the entry there,
the key pinned first, so the device, the entities and their entity ids stay
what they were and the entry is reloaded against the new address, from
setup_retry as well, with the Modbus side told to wait out the old address's
close; an entry set up with a host name is not moved, since the name follows
the unit; an address another entry has moved away from is not taken back, and
a second unit that takes it up is set up with a key of its own; and a new
entry writes its key at creation.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_device_key.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    DeadModbus,
    FakeModbus,
    FakePanel,
    _advance,
    _entity_id,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant import config_entries  # noqa: E402
from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith import modbus_api  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_DEVICE_KEY,
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)
from custom_components.ctc_ecozenith.discovery import DiscoveredDisplay, WebProbe  # noqa: E402
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

#: Where the unit turns up after a new lease, a documentation address too.
MOVED_TO = "192.0.2.77"
#: The display's own host name, the way the router's DNS may give it.
HOST_NAME = "ctc8489.lan"
#: The stand-in display's MAC, as IDENTITY has it, the way DHCP hands it over.
MAC = IDENTITY["mac"].replace(":", "")
OTHER_MAC = "020000000002"

FLOW = f"custom_components.{DOMAIN}.config_flow"


def _dhcp(ip: str, mac: str) -> DhcpServiceInfo:
    return DhcpServiceInfo(ip=ip, hostname="", macaddress=mac)


async def _discover(hass, ip: str, mac: str):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_DHCP}, data=_dhcp(ip, mac)
    )


def _the_device(hass, entry):
    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 1, "en enhet, aldrig en andra bredvid"
    return devices[0]


# ------------------------------------------------- an entry from before the key


async def test_an_entry_from_before_the_key_keeps_its_unique_ids_and_gets_its_mac(hass):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert CONF_DEVICE_KEY not in entry.data, "en befintlig post skrivs inte om vid start"

    registry = er.async_get(hass)
    outdoor = registry.async_get(_entity_id(hass, "sensor", "outdoor_temp"))
    assert outdoor.unique_id == f"{DOMAIN}_{HOST}_outdoor_temp"
    device = _the_device(hass, entry)
    assert device.identifiers == {(DOMAIN, HOST)}
    assert (dr.CONNECTION_NETWORK_MAC, IDENTITY["mac"]) in device.connections


async def test_a_mac_read_later_reaches_the_device_without_a_reload(hass):
    """The MAC read after set-up goes on the device in place.

    Only the MAC is new, so no identity sensor is added that would carry it
    on to the device by itself: the device is told directly.
    """
    without_mac = {key: value for key, value in IDENTITY.items() if key != "mac"}
    found = Identity(mac=IDENTITY["mac"])
    with patch(f"custom_components.{DOMAIN}.async_read_identity", AsyncMock(return_value=found)):
        entry = await _set_up(hass, **{CONF_IDENTITY: without_mac})
        await _let_the_background_run(hass)
    assert entry.options[CONF_IDENTITY]["mac"] == IDENTITY["mac"]
    assert (dr.CONNECTION_NETWORK_MAC, IDENTITY["mac"]) in _the_device(hass, entry).connections
    assert len(FakeModbus.instances) == 1, "ingen omladdning för identiteten"


# ---------------------------------------------------------- the DHCP flow


async def test_the_unit_at_a_new_address_moves_its_entry_and_keeps_everything(hass):
    entry = await _set_up(hass)
    registry = er.async_get(hass)
    before = {
        item.entity_id: item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert before
    outdoor = _entity_id(hass, "sensor", "outdoor_temp")
    device_id = _the_device(hass, entry).id

    result = await _discover(hass, MOVED_TO, MAC)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()

    assert entry.data[CONF_HOST] == MOVED_TO
    assert entry.data[CONF_DEVICE_KEY] == HOST, "nyckeln fästs innan adressen flyttas"
    assert entry.title == f"{MODEL} ({MOVED_TO})"
    assert entry.unique_id == f"{DOMAIN}_{HOST}", "postens unique_id står kvar"
    # Reloaded against the new address, one client at a time.
    assert entry.state is ConfigEntryState.LOADED
    assert FakeModbus.instances[-1].host == MOVED_TO
    assert len(FakeModbus.instances) == 2
    # The same device, the same entities, the same entity ids, alive.
    after = {
        item.entity_id: item.unique_id
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
    }
    assert after == before
    assert _the_device(hass, entry).id == device_id
    assert _the_device(hass, entry).identifiers == {(DOMAIN, HOST)}
    assert hass.states.get(outdoor).state == "7.2"
    assert _the_device(hass, entry).configuration_url == f"http://{MOVED_TO}/main.html"


async def test_a_move_has_the_new_address_wait_out_the_close_at_the_old(hass, monkeypatch):
    """The reload after a move is a second client of the same pump (F2.1).

    The settle book is kept per address, so the old client's close is noted on
    the old address and the new client would knock on the new one at once.
    The move says first that the two are one unit.
    """
    monkeypatch.setattr(modbus_api, "_MOVED_FROM", {})
    await _set_up(hass)
    await _discover(hass, MOVED_TO, MAC)
    await hass.async_block_till_done()
    assert modbus_api._MOVED_FROM == {(MOVED_TO, 502): HOST}


async def test_an_entry_set_up_by_host_name_stays_on_it(hass):
    """DNS follows the unit, so the lease's address is no news (F2.1).

    Home Assistant's DHCP watcher forgets what it has seen at every restart,
    and the device carries the MAC, so the first observation after each start
    asks about the unit. An entry set up by the display's host name used to be
    moved to the address, its name lost, and reloaded with a second client
    knocking on the same pump.
    """
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST_NAME}",
        title=f"{MODEL} ({HOST_NAME})",
        data={
            CONF_HOST: HOST_NAME,
            CONF_DEVICE_KEY: HOST_NAME,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    result = await _discover(hass, MOVED_TO, MAC)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == HOST_NAME
    assert entry.title == f"{MODEL} ({HOST_NAME})"
    assert [client.host for client in FakeModbus.instances] == [HOST_NAME], "ingen omladdning"


async def test_the_unit_where_it_already_is_changes_nothing(hass):
    entry = await _set_up(hass)
    result = await _discover(hass, HOST, MAC)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == HOST
    assert CONF_DEVICE_KEY not in entry.data
    assert len(FakeModbus.instances) == 1, "ingen omladdning"


async def test_an_address_a_moved_entry_left_is_not_taken_back(hass):
    entry = await _set_up(hass)
    await _discover(hass, MOVED_TO, MAC)
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == MOVED_TO

    # Another device answers from the old address: the entry stays where its unit is.
    with patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=None)):
        result = await _discover(hass, HOST, OTHER_MAC)
    assert result["type"] is FlowResultType.ABORT
    await hass.async_block_till_done()
    assert entry.data[CONF_HOST] == MOVED_TO


def _display_flow_patches(display: DiscoveredDisplay):
    """A display that answers at ``display.host``, and a menu reading that finds nothing."""
    return (
        patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=display)),
        patch(f"{FLOW}.async_probe_web", AsyncMock(return_value=WebProbe(display, answered=True))),
        patch(f"{FLOW}.async_discover_pages", AsyncMock(return_value=MenuReading())),
        patch(f"{FLOW}.CtcWebClient", FakePanel),
    )


def _own_key_holds(hass, first, second) -> None:
    """The second unit has a key, a device and unique_ids of its own, beside the first's."""
    key = f"{HOST}#2"
    assert second.data[CONF_HOST] == HOST
    assert second.data[CONF_DEVICE_KEY] == key
    assert second.unique_id == f"{DOMAIN}_{key}"
    assert first.data[CONF_DEVICE_KEY] == HOST and first.unique_id == f"{DOMAIN}_{HOST}"
    assert second.state is ConfigEntryState.LOADED
    assert first.state is ConfigEntryState.LOADED
    assert _the_device(hass, second).identifiers == {(DOMAIN, key)}
    assert _the_device(hass, first).identifiers == {(DOMAIN, HOST)}
    registry = er.async_get(hass)
    ours = [item.unique_id for item in er.async_entries_for_config_entry(registry, second.entry_id)]
    theirs = [item.unique_id for item in er.async_entries_for_config_entry(registry, first.entry_id)]
    assert ours and theirs
    assert all(unique.startswith(f"{DOMAIN}_{key}_") for unique in ours)
    assert all(unique.startswith(f"{DOMAIN}_{HOST}_") for unique in theirs)
    assert f"{DOMAIN}_{key}_outdoor_temp" in ours
    assert f"{DOMAIN}_{HOST}_outdoor_temp" in theirs


async def test_a_second_unit_found_at_the_moved_entrys_first_address_gets_its_own_key(hass):
    """DHCP finds another CTC where a moved entry was created (F2.3).

    The moved entry keeps that address's key and unique_id, so the second
    unit was told it was set up already, and would have had the first one's
    device and unique_ids had it got through.
    """
    first = await _set_up(hass)
    await _discover(hass, MOVED_TO, MAC)
    await hass.async_block_till_done()
    assert first.data[CONF_HOST] == MOVED_TO

    display = DiscoveredDisplay(HOST, "settings_ezi2xx.bin")
    probe, web, pages, panel = _display_flow_patches(display)
    with probe, web, pages, panel:
        result = await _discover(hass, HOST, OTHER_MAC)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "confirm"
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    (second,) = [entry for entry in hass.config_entries.async_entries(DOMAIN) if entry is not first]
    _own_key_holds(hass, first, second)


async def test_a_second_unit_typed_at_the_moved_entrys_first_address_gets_its_own_key(hass):
    first = await _set_up(hass)
    await _discover(hass, MOVED_TO, MAC)
    await hass.async_block_till_done()

    display = DiscoveredDisplay(HOST, "settings_ezi2xx.bin")
    probe, web, pages, panel = _display_flow_patches(display)
    with probe, web, pages, panel:
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    (second,) = [entry for entry in hass.config_entries.async_entries(DOMAIN) if entry is not first]
    _own_key_holds(hass, first, second)


async def test_a_second_unit_ignored_at_the_moved_entrys_first_address_stays_ignored(hass):
    """Its own key is the same at every discovery, so ignoring it holds."""
    await _set_up(hass)
    await _discover(hass, MOVED_TO, MAC)
    await hass.async_block_till_done()

    display = DiscoveredDisplay(HOST, "settings_ezi2xx.bin")
    with patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=display)):
        result = await _discover(hass, HOST, OTHER_MAC)
        assert result["step_id"] == "confirm"
        await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_IGNORE},
            data={"unique_id": f"{DOMAIN}_{HOST}#2", "title": "CTC"},
        )
        await hass.async_block_till_done()
        result = await _discover(hass, HOST, OTHER_MAC)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_an_unknown_unit_is_still_offered(hass):
    await _set_up(hass)
    display = DiscoveredDisplay(MOVED_TO, "settings_ezi2xx.bin")
    with patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=display)):
        result = await _discover(hass, MOVED_TO, OTHER_MAC)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    hass.config_entries.flow.async_abort(result["flow_id"])


async def test_an_entry_waiting_to_be_set_up_again_is_tried_at_its_new_address(hass, monkeypatch):
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry = await _set_up(hass)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        # The unit is back, at another address, and DHCP says so before the
        # back-off is over.
        monkeypatch.setattr(DeadModbus, "answers", True)
        await _discover(hass, MOVED_TO, MAC)
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert FakeModbus.instances[-1].host == MOVED_TO
    assert entry.data[CONF_DEVICE_KEY] == HOST
    assert er.async_get(hass).async_get(_entity_id(hass, "sensor", "outdoor_temp")).unique_id == (
        f"{DOMAIN}_{HOST}_outdoor_temp"
    )


@pytest.mark.parametrize("mac", [MAC, OTHER_MAC], ids=["by-mac", "by-address"])
async def test_an_entry_waiting_to_be_set_up_again_is_tried_when_its_unit_asks_for_its_address(
    hass, monkeypatch, mac
):
    """A power cut that brings Home Assistant up before the heat pump.

    The entry waits out its back-off, up to ten minutes. The display's first
    DHCP request says the unit is back where it was, and the entry is tried
    at once, as Home Assistant does for a discovery of a unique_id it knows;
    the flow that recognises the unit by its MAC or its address used to abort
    before that rule was reached. A unit whose MAC was never read is
    recognised by its address alone.
    """
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        identity = IDENTITY if mac == MAC else {k: v for k, v in IDENTITY.items() if k != "mac"}
        entry = await _set_up(hass, **{CONF_IDENTITY: identity})
        assert entry.state is ConfigEntryState.SETUP_RETRY
        monkeypatch.setattr(DeadModbus, "answers", True)
        result = await _discover(hass, HOST, mac)
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"
        await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.data[CONF_HOST] == HOST
    assert CONF_DEVICE_KEY not in entry.data, "posten flyttas inte, den väcks bara"


async def test_a_loaded_entry_is_not_reloaded_when_its_unit_asks_for_its_address(hass):
    entry = await _set_up(hass)
    with patch.object(hass.config_entries, "async_schedule_reload") as reload:
        result = await _discover(hass, HOST, MAC)
        await hass.async_block_till_done()
    assert result["reason"] == "already_configured"
    reload.assert_not_called()
    assert entry.state is ConfigEntryState.LOADED
    assert len(FakeModbus.instances) == 1


# ------------------------------------------------------------- a new entry


async def test_a_new_entry_writes_its_key_and_a_moved_address_is_already_configured(hass):
    display = DiscoveredDisplay(HOST, "settings_ezi2xx.bin")
    with (
        patch(f"{FLOW}.async_home_assistant_networks", AsyncMock(return_value=[])),
        patch(f"{FLOW}.async_discover", AsyncMock(return_value=[])),
        patch(f"{FLOW}.async_probe_host", AsyncMock(return_value=display)),
        # A typed address asks the web port first (R11): it answers.
        patch(f"{FLOW}.async_probe_web", AsyncMock(return_value=WebProbe(display, answered=True))),
        patch(f"{FLOW}.async_discover_pages", AsyncMock(return_value=MenuReading())),
        patch(f"{FLOW}.CtcWebClient", FakePanel),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # The set-up starts with a choice (R70): enter an address.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manual"}
        )
        assert result["step_id"] == "manual"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        (entry,) = hass.config_entries.async_entries(DOMAIN)
        assert entry.data[CONF_DEVICE_KEY] == HOST
        assert entry.data[CONF_HOST] == HOST

        # The unit moves; somebody then types its new address by hand.
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_HOST: MOVED_TO}
        )
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"next_step_id": "manual"}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: MOVED_TO, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
