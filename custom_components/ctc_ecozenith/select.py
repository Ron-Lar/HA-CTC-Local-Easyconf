"""Mode controls backed by CTC's volatile 1000 block."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import CtcConfigEntry
from .attribute_names import with_english
from .const import CONTROL_SELECTS, ControlRegister
from .keys import unique_id
from .entity import async_switch_on_new_defaults
from .modbus_api import CtcModbusError

_LOGGER = logging.getLogger(__name__)

RELEASE = "Släpp styrningen"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.control_enabled:
        return
    entities = [CtcControlSelect(runtime, register) for register in CONTROL_SELECTS]
    async_switch_on_new_defaults(hass, entry, "select", entities)
    async_add_entities(entities)


class CtcControlSelect(SelectEntity):
    """A volatile control register with a fixed set of modes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, register: ControlRegister) -> None:
        self._runtime = runtime
        self._register = register
        self._enum = register.enum or {}
        self._attr_unique_id = unique_id(runtime.device, register.key)
        self._attr_name = register.name
        self._attr_device_info = runtime.device
        self._attr_options = [RELEASE, *self._enum.values()]
        if register.icon:
            self._attr_icon = register.icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Written whenever an override changes, including "Släpp all styrning".
        self.async_on_remove(
            self._runtime.control.async_add_listener(self.async_write_ha_state)
        )

    @property
    def current_option(self) -> str:
        raw = self._runtime.control.get(self._register.address)
        if raw is None:
            return RELEASE
        return self._enum.get(raw, RELEASE)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        attributes = {
            "register": str(self._register.address),
            "not": "flyktigt register, nollställs av pumpen cirka fem minuter efter sista skrivningen",
        }
        # When the controller last took the value, and until when it holds it,
        # beside the English twins last_written and valid_until.
        attributes.update(self._runtime.control.written_attributes(self._register.address))
        return with_english(attributes)

    async def async_select_option(self, option: str) -> None:
        if option == RELEASE:
            await self._runtime.control.async_set(self._register.address, None)
            return
        for raw, label in self._enum.items():
            if label == option:
                try:
                    await self._runtime.control.async_set(self._register.address, raw)
                except CtcModbusError as err:
                    _LOGGER.error("Could not write %s: %s", self._register.name, err)
                    raise
                return
