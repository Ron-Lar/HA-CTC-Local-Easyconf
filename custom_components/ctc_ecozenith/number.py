"""Control entities backed by CTC's volatile 1000 block.

Writing here is the safe way to steer the unit. The registers are not stored in
EEPROM, so they can be written as often as wanted, and the controller drops them
about five minutes after the last write. Setting an entity back to its own
"release" position stops the override and hands control back to the heat pump.

The stored settings in the 61500 block are deliberately never written. CTC warns
that the number of write cycles there is limited and that frequent writes can
destroy the controller.
"""

from __future__ import annotations

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import (
    EntityCategory,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .attribute_names import with_english
from .const import CONTROL_NUMBERS, ControlRegister
from .entity import async_switch_on_new_defaults
from .keys import unique_id
from .modbus_api import CtcModbusError
from .overrides import pump_number

_LOGGER = logging.getLogger(__name__)

UNITS = {
    "°C": UnitOfTemperature.CELSIUS,
    "kW": UnitOfPower.KILO_WATT,
    "h": UnitOfTime.HOURS,
    "rps": "rps",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if not runtime.control_enabled:
        return
    entities = [CtcControlNumber(runtime, register) for register in CONTROL_NUMBERS]
    async_switch_on_new_defaults(hass, entry, "number", entities)
    async_add_entities(entities)


class CtcControlNumber(CoordinatorEntity, NumberEntity):
    """One volatile control register exposed as a number.

    Follows the Modbus coordinator because, with no override in force, it shows
    the unit's own setting, which someone can change at the panel at any time.
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, runtime, register: ControlRegister) -> None:
        super().__init__(runtime.modbus)
        self._runtime = runtime
        self._register = register
        self._attr_unique_id = unique_id(runtime.device, register.key)
        self._attr_name = register.name
        self._attr_device_info = runtime.device
        self._attr_native_min_value = register.minimum
        self._attr_native_max_value = register.maximum
        self._attr_native_step = register.step
        self._attr_native_unit_of_measurement = UNITS.get(
            register.unit or "", register.unit
        )
        if register.icon:
            self._attr_icon = register.icon

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # "Släpp all styrning" changes what this shows without writing through it.
        self.async_on_remove(
            self._runtime.control.async_add_listener(self.async_write_ha_state)
        )

    @property
    def available(self) -> bool:
        """Available whatever the last Modbus round did, like the selects (F2.1).

        Home Assistant skips an unavailable entity in an action, so an energy
        manager or an automation that sets a value during a round that failed
        would have it dropped with no more than a warning in the log, and an
        energy manager that counts the call as sent may hold off for an hour.
        An unavailable entity also loses its attributes, so a held number would
        stop saying that it is held while the sensor Aktiva överstyrningar
        still counts it. The write itself says whether it reached the pump; a
        lost round only takes the mirror away, and an idle number then shows
        unknown until the next round answers. Not only while held: an idle
        number would otherwise drop the first value set after a release.
        """
        return True

    @property
    def native_value(self) -> float | None:
        """Return the override in force, or the unit's own setting when idle."""
        raw = self._runtime.control.get(self._register.address)
        if raw is not None:
            return round(raw * self._register.scale, 3)
        return pump_number(self._runtime.modbus, self._register)

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        control = self._runtime.control
        overriding = self._register.address in control.active
        attributes = {
            "styrning aktiv": "ja" if overriding else "nej",
            # The English twin is a boolean rather than a Swedish word.
            "control_active": overriding,
            "register": str(self._register.address),
            "not": (
                "flyktigt register, nollställs av pumpen cirka fem minuter efter sista skrivningen"
            ),
        }
        if self._register.mirror is not None:
            # The pump's own setting beside what Home Assistant holds (R49),
            # also while it holds something else: the card shows both. Only
            # where there is a mirror; the hot water setpoint has none.
            attributes["pumpens värde"] = pump_number(self._runtime.modbus, self._register)
        # "Aktiv" is claimed only from a write that reached the controller, and
        # these two say when that was and how long the unit holds it.
        attributes.update(control.written_attributes(self._register.address))
        return with_english(attributes)

    async def async_set_native_value(self, value: float) -> None:
        raw = round(value / self._register.scale)
        try:
            await self._runtime.control.async_set(self._register.address, raw)
        except CtcModbusError as err:
            _LOGGER.error("Could not write %s: %s", self._register.name, err)
            raise
