"""The integration's actions, for automations and energy managers (roadmap R76).

Three, registered once for the domain from async_setup:

* ``release`` lets go of one or more control registers, given as their number
  or select entities. Nothing is written: Home Assistant stops writing, and the
  controller goes back to its own setting about five minutes after the last
  write it took, exactly as "Släpp all styrning" does for every register at
  once. A select could already be released through its own option; a number
  had no way back but the button that releases everything, so an energy
  manager that lets go of a power limit used to let go of the hot water as well.
* ``reread_menu`` reads the display's menu again, the reading "read the menu
  again" in the options makes, for one entry or every loaded entry with a
  display. A panel that is in use is said at once and never waited for. Where
  some entries were read and others not, the answer names all three kinds.
* ``cop_sample`` writes the two lifetime counters down as a sample of the
  coefficient of performance now, rather than at the six hour timer, and
  answers with the counters.

The write guard is untouched: release writes nothing, and nothing here
reaches the Modbus client other than through the control manager. The menu
is walked only under the entry's one panel lock, like every other walk.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.loader import async_get_integration
import voluptuous as vol

from .catalogue import PanelBusy
from .const import CONTROL_BY_KEY, DOMAIN, ControlRegister, has_display
from .cop import sample_summary
from .keys import key_of

_LOGGER = logging.getLogger(__name__)

SERVICE_RELEASE = "release"
SERVICE_REREAD_MENU = "reread_menu"
SERVICE_COP_SAMPLE = "cop_sample"
SERVICES = (SERVICE_RELEASE, SERVICE_REREAD_MENU, SERVICE_COP_SAMPLE)

ATTR_CONFIG_ENTRY_ID = "config_entry_id"

#: The entity platforms a control register is one of.
CONTROL_PLATFORMS = ("number", "select")

#: What a list of entries in an answer reads as when it has none in it. A
#: placeholder is put in as it is, whatever the language of the text around
#: it, so a word would be English in a Swedish sentence; a count is neither.
NONE_LISTED = "0"

RELEASE_SCHEMA = vol.Schema({vol.Required(ATTR_ENTITY_ID): cv.entity_ids})
ENTRY_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})


def _refuse(key: str, **placeholders: str) -> ServiceValidationError:
    """A refusal of what the action was asked, in the user's language."""
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _failed(key: str, **placeholders: str) -> HomeAssistantError:
    """An action that was asked rightly and could not be done, in the user's language."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders or None,
    )


def _listed(titles: list[str]) -> str:
    """The entries of one kind in an answer, by title, or NONE_LISTED for none."""
    return ", ".join(titles) if titles else NONE_LISTED


def _loaded(entry: ConfigEntry | None) -> bool:
    return (
        entry is not None
        and entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is not None
    )


def _loaded_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Every entry of the integration that is loaded, in Home Assistant's order."""
    return [entry for entry in hass.config_entries.async_entries(DOMAIN) if _loaded(entry)]


def _named_entry(hass: HomeAssistant, entry_id: str) -> ConfigEntry:
    """The one loaded entry an action was pointed at, or a refusal that says why not."""
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN:
        raise _refuse("unknown_entry", entry_id=entry_id)
    if not _loaded(entry):
        raise _refuse("entry_not_loaded", entry=entry.title)
    return entry


def _control_of(hass: HomeAssistant, entity_id: str) -> tuple[Any, ControlRegister]:
    """The runtime and the control register behind one number or select entity.

    Looked up through the entity registry, by the key in the entity's
    unique_id (keys.key_of), never by its entity id, which the owner may have
    renamed. Anything else, an entity of another integration, a sensor, a
    control of an entry that is not loaded or that has control switched off,
    is refused before anything is released.
    """
    item = er.async_get(hass).async_get(entity_id)
    if item is None or item.platform != DOMAIN or item.domain not in CONTROL_PLATFORMS:
        raise _refuse("not_a_control", entity_id=entity_id)
    entry = hass.config_entries.async_get_entry(item.config_entry_id or "")
    if not _loaded(entry):
        raise _refuse("not_a_control", entity_id=entity_id)
    runtime = entry.runtime_data  # type: ignore[union-attr]
    register = CONTROL_BY_KEY.get(key_of(item.unique_id, runtime.device) or "")
    if register is None or not runtime.control_enabled:
        raise _refuse("not_a_control", entity_id=entity_id)
    return runtime, register


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the three actions, unless an earlier set-up already has."""
    if hass.services.has_service(DOMAIN, SERVICE_RELEASE):
        return

    async def _release(call: ServiceCall) -> None:
        """Stop holding each given control, writing nothing."""
        # Every entity is checked before any is released, so a list with one
        # wrong entry in it changes nothing rather than half of what it names.
        targets = [_control_of(hass, entity_id) for entity_id in call.data[ATTR_ENTITY_ID]]
        for runtime, register in targets:
            await runtime.control.async_set(register.address, None)

    async def _reread_menu(call: ServiceCall) -> None:
        """Read the menu again, for one entry with a display or for each of them."""
        # Imported here rather than at the top, so this module never needs the
        # package's __init__ to have finished loading when it is imported.
        from . import async_reread_menu_now

        wanted = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if wanted:
            entry = _named_entry(hass, wanted)
            if not has_display(entry.data):
                raise _refuse("no_display", entry=entry.title)
            entries = [entry]
        else:
            entries = [entry for entry in _loaded_entries(hass) if has_display(entry.data)]
            if not entries:
                raise _refuse("no_display_loaded")
        version = str((await async_get_integration(hass, DOMAIN)).version)
        read: list[str] = []
        busy: list[str] = []
        missed: list[str] = []
        for entry in entries:
            if not _loaded(entry):
                # Unloaded while an earlier entry's menu was walked.
                missed.append(entry.title)
                continue
            try:
                written = await async_reread_menu_now(hass, entry, version)
            except PanelBusy:
                busy.append(entry.title)
                continue
            (read if written else missed).append(entry.title)
        if not busy and not missed:
            return
        # Every kind is named, the missed beside the taken, so whoever asks
        # again can ask for the entries that were not read on their own
        # (config_entry_id) instead of walking a panel that was just read
        # once more. A menu that was written is reloading its entry, so an
        # answer with one read never says that nothing has changed.
        read_list, busy_list, missed_list = _listed(read), _listed(busy), _listed(missed)
        if read:
            raise _failed("menu_partly_read", read=read_list, busy=busy_list, missed=missed_list)
        if busy and missed:
            raise _failed("menu_busy_and_not_read", busy=busy_list, missed=missed_list)
        if busy:
            raise _failed("panel_busy", entries=busy_list)
        raise _failed("menu_not_read", entries=missed_list)

    async def _cop_sample(call: ServiceCall) -> ServiceResponse:
        """Take a sample of the counters now, and answer with them per entry."""
        wanted = call.data.get(ATTR_CONFIG_ENTRY_ID)
        if wanted:
            entry = _named_entry(hass, wanted)
            if entry.runtime_data.cop is None:
                raise _refuse("no_counters", entry=entry.title)
            entries = [entry]
        else:
            entries = [
                entry for entry in _loaded_entries(hass) if entry.runtime_data.cop is not None
            ]
            if not entries:
                raise _refuse("no_counters_loaded")
        answer: dict[str, Any] = {}
        for entry in entries:
            runtime = entry.runtime_data
            recorded = await runtime.async_record_cop()
            answer[entry.entry_id] = sample_summary(runtime, recorded)
        return answer

    hass.services.async_register(DOMAIN, SERVICE_RELEASE, _release, schema=RELEASE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REREAD_MENU, _reread_menu, schema=ENTRY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_COP_SAMPLE,
        _cop_sample,
        schema=ENTRY_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
