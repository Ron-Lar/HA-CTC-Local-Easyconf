"""Mode controls backed by CTC's volatile 1000 block."""

from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.const import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .attribute_names import with_english
from .const import CONTROL_SELECTS, ControlRegister
from .keys import unique_id
from .entity import async_switch_on_new_defaults
from .modbus_api import CtcModbusError
from .overrides import pump_option

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


class CtcControlSelect(CoordinatorEntity, SelectEntity):
    """A volatile control register with a fixed set of modes.

    Follows the Modbus coordinator, like the numbers, because with no override
    in force a select that has a mirror shows the pump's own mode (R49), the
    hot water mode stored in 61500, which someone can change at the panel at
    any time. "Släpp styrningen" stays among the options for letting go; it is
    what a select without a mirror shows while idle, and what one with a
    mirror shows while the mirror cannot be read.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, register: ControlRegister) -> None:
        super().__init__(runtime.modbus)
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
    def available(self) -> bool:
        """Available whatever the last Modbus round did, as before it followed one.

        Home Assistant skips an unavailable entity in an action, so an energy
        manager or an automation that picks a mode during a round that failed
        would have its choice dropped without a word. The write itself says
        whether it reached the pump; a lost round only takes the mirror away,
        and the select then shows "Släpp styrningen" while idle.
        """
        return True

    @property
    def current_option(self) -> str:
        raw = self._runtime.control.get(self._register.address)
        if raw is not None:
            return self._enum.get(raw, RELEASE)
        return pump_option(self._runtime.modbus, self._register) or RELEASE

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        control = self._runtime.control
        overriding = self._register.address in control.active
        attributes = {
            "styrning aktiv": "ja" if overriding else "nej",
            # The English twin is a boolean rather than a Swedish word, as on
            # the numbers.
            "control_active": overriding,
            "register": str(self._register.address),
            "not": "flyktigt register, nollställs av pumpen cirka fem minuter efter sista skrivningen",
        }
        if self._register.mirror is not None:
            # The pump's own mode beside what Home Assistant holds (R49). Not
            # for the zone mode: 61542 has other modes and does not mirror 1015.
            attributes["pumpens värde"] = pump_option(self._runtime.modbus, self._register)
        # When the controller last took the value, and until when it holds it,
        # beside the English twins last_written and valid_until.
        attributes.update(control.written_attributes(self._register.address))
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
