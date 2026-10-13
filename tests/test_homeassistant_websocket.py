"""The section card's websocket command under a real Home Assistant core (L11).

ctc_ecozenith/items answers the section card with one section of the CTC page.
Here the command runs through Home Assistant's own websocket server, asked by a
user who is not an administrator, since anyone signed in may open the page:
the answer has to be the very cards the page has in that section, built from
the same description of the pump and in the page's language, also for a second
pump whose panel speaks another, and nothing else; a device that is no heat
pump of this integration, a heat pump whose entry is not running and a section
the card does not know are refused, each in its own words.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_websocket.py
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    IDENTITY,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith import dashboard  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_LANGUAGE,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
    LANG_ENGLISH,
)

COMMAND = f"{DOMAIN}/items"

#: A second heat pump, on another documentation address (RFC 5737), with a
#: panel read in English and an identity of its own, so that it is a device of
#: its own and its name sorts after the first one's.
OTHER_HOST = "192.0.2.56"
OTHER_MODEL = "EcoZenith i550 Pro"


@pytest.fixture
async def client(hass, stubs, hass_ws_client, hass_read_only_access_token):
    """A websocket client of a user who is not an administrator, with one pump set up.

    The shared stubs count the http component as set up, which the websocket
    server needs for real; it is set up here instead, as Home Assistant would.
    The stubs put the name in with set.update, past the component set's own
    bookkeeping, so it comes out the same way.
    """
    set.discard(hass.config.components, "http")
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    socket = await hass_ws_client(hass, hass_read_only_access_token)
    socket.entry = entry
    return socket


def _device_id(hass, entry) -> str:
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    return device.id


async def _ask(client, device_id: str, section: str) -> dict:
    await client.send_json_auto_id({"type": COMMAND, "device_id": device_id, "section": section})
    return await client.receive_json()


async def _set_up_a_second_pump_in_english(hass) -> MockConfigEntry:
    """An i550 Pro beside the first pump, its panel read in English."""
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{OTHER_HOST}",
        title=f"{OTHER_MODEL} ({OTHER_HOST})",
        data={
            CONF_HOST: OTHER_HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": OTHER_MODEL,
        },
        options={
            CONF_MENU_VERSION: version,
            CONF_LANGUAGE: LANG_ENGLISH,
            CONF_IDENTITY: {**IDENTITY, "serial": "720825400001", "mac": "02:00:00:00:00:02"},
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _page_cards(hass, tab: str, heading: str) -> list[dict]:
    """The cards of one section of the page as the sidebar would serve it now."""
    config = json.loads(json.dumps(dashboard.build_config(hass)))
    view = next(v for v in config["views"] if v["path"] == tab)
    found = [s for s in view["sections"] if s["cards"][0]["heading"] == heading]
    return found[0]["cards"][1:] if found else []


async def test_a_user_who_is_not_an_admin_gets_the_section_as_the_page_has_it(hass, client):
    device_id = _device_id(hass, client.entry)
    reply = await _ask(client, device_id, "temperatures")
    assert reply["success"], reply
    cards = reply["result"]["cards"]
    assert set(reply["result"]) == {"cards"}, "inget utöver det sidan visar"
    assert cards == _page_cards(hass, "performance", "Temperaturer")
    (rows,) = cards
    assert rows["type"] == "custom:ctc-ecozenith-rows"
    entities = [row["entity"] for row in rows["rows"]]
    assert _entity_id(hass, "sensor", "outdoor_temp") in entities
    assert all(row["explanation"] and row["source"] for row in rows["rows"])


async def test_the_status_and_a_control_section_are_the_pages_too(hass, client):
    device_id = _device_id(hass, client.entry)
    status = await _ask(client, device_id, "status")
    assert status["success"], status
    (chips,) = status["result"]["cards"]
    assert chips["type"] == "custom:ctc-ecozenith-chips"
    title = dr.async_get(hass).async_get(device_id).name
    assert status["result"]["cards"] == _page_cards(hass, "overview", title)

    heating = await _ask(client, device_id, "heating_controls")
    assert heating["success"], heating
    assert heating["result"]["cards"] == _page_cards(hass, "controls", "Värme")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    assert room in [item["entity"] for item in heating["result"]["cards"][0]["items"]]


async def test_a_second_pump_with_an_english_panel_reads_in_the_pages_words(hass, client):
    # The page builds every pump in the first one's language, by name: the
    # i255's Swedish. The section of the i550 Pro, whose panel is read in
    # English, has to be the page's to the letter, not built in its own.
    other = await _set_up_a_second_pump_in_english(hass)
    assert other.state is ConfigEntryState.LOADED
    first_id, other_id = _device_id(hass, client.entry), _device_id(hass, other)
    assert first_id != other_id
    devices = dr.async_get(hass)
    first_name, other_name = (devices.async_get(i).name.casefold() for i in (first_id, other_id))
    assert first_name < other_name
    assert [pump["language"] for pump in dashboard._collect(hass, other.entry_id)] == ["en"]

    reply = await _ask(client, other_id, "temperatures")
    assert reply["success"], reply
    page = _page_cards(hass, "performance-2", "Temperaturer")
    assert page, "sidan har sektionen för den andra pumpen"
    assert reply["result"]["cards"] == page
    (rows,) = reply["result"]["cards"]
    assert rows["more_info"] == "Mer info"
    assert rows["explain"] == "Förklaring"

    values = await _ask(client, other_id, "values")
    assert values["success"], values
    page = _page_cards(hass, "values-2", "Alla värden")
    assert page, "sidan har alla värden för den andra pumpen"
    assert values["result"]["cards"] == page
    assert values["result"]["cards"][0]["filter"] == "Sök bland värdena"

    # The first pump's section is the page's as before.
    first = await _ask(client, first_id, "temperatures")
    assert first["success"], first
    assert first["result"]["cards"] == _page_cards(hass, "performance-1", "Temperaturer")


async def test_a_device_that_is_not_one_of_ours_is_refused(hass, client):
    reply = await _ask(client, "no-such-device", "energy")
    assert not reply["success"]
    assert reply["error"]["code"] == "not_found"

    other = MockConfigEntry(domain="other_integration")
    other.add_to_hass(hass)
    lamp = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("other_integration", "lamp")}
    )
    reply = await _ask(client, lamp.id, "energy")
    assert not reply["success"]
    assert reply["error"]["code"] == "not_found"


async def test_a_heat_pump_whose_entry_is_not_running_is_refused_as_such(hass, client):
    device_id = _device_id(hass, client.entry)
    assert await hass.config_entries.async_unload(client.entry.entry_id)
    await hass.async_block_till_done()
    assert client.entry.state is ConfigEntryState.NOT_LOADED
    reply = await _ask(client, device_id, "energy")
    assert not reply["success"]
    assert reply["error"]["code"] == "not_running"


async def test_a_section_the_card_does_not_know_is_refused(hass, client):
    reply = await _ask(client, _device_id(hass, client.entry), "graph_cop")
    assert not reply["success"]
    assert reply["error"]["code"] == "invalid_format"
