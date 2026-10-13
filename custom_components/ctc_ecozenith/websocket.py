"""The websocket command behind the section card (roadmap L11).

custom:ctc-ecozenith-section puts one section of the CTC page on a dashboard of
one's own. It names a heat pump by its device id and a section by its id, and
asks ctc_ecozenith/items for the cards to draw; section_card.py cuts them out of
the page as the page would be built this moment, from the description of the
pump the page itself is built from (dashboard._collect), in the language the
page is built in: the first running pump's, in the page's order, worked out
over every running pump (dashboard_views.page_language).

Anyone signed in may ask, as anyone signed in may open the page, and the answer
holds nothing the page does not show. The device has to be a heat pump of this
integration whose entry is running, since a pump that is not running has no
page either; the section has to be one the card knows, which the schema checks.
"""

from __future__ import annotations

from typing import Any, Final

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
import voluptuous as vol

from . import dashboard
from .const import DOMAIN
from .dashboard_views import page_language
from .section_card import SECTIONS, section_cards

COMMAND: Final = f"{DOMAIN}/items"

#: The device is not one of this integration's heat pumps.
ERR_NOT_FOUND: Final = websocket_api.ERR_NOT_FOUND
#: The device is one of ours, but its entry is not running: retried, switched
#: off or still starting. The card says so and asks again later.
ERR_NOT_RUNNING: Final = "not_running"


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Make the section card's command known. Once per run, from async_setup."""
    websocket_api.async_register_command(hass, ws_items)


def _entries_of(hass: HomeAssistant, device_id: str) -> list[ConfigEntry]:
    """This integration's entries behind a device, or none for any other device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []
    found = []
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None and entry.domain == DOMAIN:
            found.append(entry)
    return found


def _running(entry: ConfigEntry) -> bool:
    return (
        entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is not None
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): COMMAND,
        vol.Required("device_id"): str,
        vol.Required("section"): vol.In(list(SECTIONS)),
    }
)
@callback
def ws_items(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]
) -> None:
    """The cards of one section of one heat pump's page, as the page has them."""
    entries = _entries_of(hass, msg["device_id"])
    if not entries:
        connection.send_error(
            msg["id"], ERR_NOT_FOUND, "The device is not a heat pump of CTC Local Easyconf"
        )
        return
    running = next((entry for entry in entries if _running(entry)), None)
    pumps = dashboard._collect(hass, running.entry_id) if running is not None else []
    if not pumps:
        connection.send_error(msg["id"], ERR_NOT_RUNNING, "The heat pump is not running")
        return
    # The page builds every pump in the first one's language, by name, so the
    # section asks for the same: a second pump whose panel speaks another
    # language reads here in the page's words. The pump asked for is running,
    # so the whole list has it, and the fallback is never reached.
    lang = page_language(dashboard._collect(hass), pumps[0].get("language"))
    connection.send_result(msg["id"], {
        "cards": section_cards(
            pumps[0], msg["section"], (MAJOR_VERSION, MINOR_VERSION), lang=lang
        ),
    })
