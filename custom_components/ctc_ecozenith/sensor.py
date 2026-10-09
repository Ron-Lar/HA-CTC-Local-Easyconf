"""Sensors from both transports."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.const import EntityCategory

from . import CtcConfigEntry
from .catalogue import display_state_class
from .cop import (
    cop_reason,
    current_totals,
    lifetime_ratio,
    modbus_consumption_answered,
    powered_on_hours,
)
from .const import DOMAIN, STATUS_UNKNOWN, ModbusSensor, SlowValue, device_class_for

# By the names const.py uses, so the choice of class can be tested without
# Home Assistant installed.
DEVICE_CLASSES = {
    "temperature": SensorDeviceClass.TEMPERATURE,
    "power": SensorDeviceClass.POWER,
    "energy": SensorDeviceClass.ENERGY,
    "current": SensorDeviceClass.CURRENT,
    "voltage": SensorDeviceClass.VOLTAGE,
    "pressure": SensorDeviceClass.PRESSURE,
    "duration": SensorDeviceClass.DURATION,
}

UNITS = {
    "°C": UnitOfTemperature.CELSIUS,
    "kW": UnitOfPower.KILO_WATT,
    "kWh": UnitOfEnergy.KILO_WATT_HOUR,
    "A": UnitOfElectricCurrent.AMPERE,
    "V": UnitOfElectricPotential.VOLT,
    "bar": UnitOfPressure.BAR,
    "h": UnitOfTime.HOURS,
    "min": UnitOfTime.MINUTES,
    "l/min": UnitOfVolumeFlowRate.LITERS_PER_MINUTE,
    "%": "%",
    "rps": "rps",
    "ppm": "ppm",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create every sensor for this heat pump."""
    runtime = entry.runtime_data
    entities: list[SensorEntity] = [
        CtcModbusSensor(runtime, description)
        for description in runtime.modbus.descriptions
    ]
    if runtime.web is not None:
        for page in runtime.pages:
            for value in page.values:
                entities.append(CtcDisplaySensor(runtime, page.title, value))

    # What the unit is: read once from the display and then unchanging.
    for key, name, value, icon in (
        ("hp_model", "Värmepumpsmodell", runtime.identity.heatpump_model, "mdi:heat-pump-outline"),
        ("display_fw", "Programversion display", runtime.identity.display_firmware, "mdi:chip"),
        ("hp_fw", "Programversion VP-styrkort", runtime.identity.heatpump_firmware, "mdi:chip"),
        ("bootloader", "Bootloaderversion", runtime.identity.bootloader, "mdi:chip"),
        ("serial", "Serienummer", runtime.identity.serial, "mdi:identifier"),
        ("made", "Tillverkad", runtime.identity.manufactured, "mdi:factory"),
    ):
        if value:
            entities.append(CtcIdentitySensor(runtime, key, name, value, icon))

    if runtime.cop is not None:
        for span in ("day", "year", "first_year", "lifetime"):
            entities.append(CtcCopSensor(runtime, span))

    async_add_entities(entities)


class CtcModbusSensor(CoordinatorEntity, SensorEntity):
    """One documented Modbus register."""

    _attr_has_entity_name = True

    def __init__(self, runtime, description: ModbusSensor) -> None:
        super().__init__(runtime.modbus)
        self._description = description
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = runtime.device
        self._attr_entity_registry_enabled_default = description.enabled_default
        if description.icon:
            self._attr_icon = description.icon
        if description.diagnostic:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if description.enum is None:
            self._attr_native_unit_of_measurement = UNITS.get(
                description.unit or "", description.unit
            )
            self._attr_device_class = DEVICE_CLASSES.get(
                device_class_for(description.unit, description.device_class) or ""
            )
            if description.state_class == "total_increasing":
                self._attr_state_class = SensorStateClass.TOTAL_INCREASING
            elif description.state_class == "measurement":
                self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_device_class = SensorDeviceClass.ENUM
            # The reading for a code with no label is an option like any other,
            # because Home Assistant rejects a state that is not among them.
            self._attr_options = [*description.enum.values(), STATUS_UNKNOWN]

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._description.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._description.key in (self.coordinator.data or {})
        )

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """The code behind a reading the table has no label for, and nothing else.

        Without it the number the controller actually answered with would be lost
        to the log, and that number is what a label is eventually written from.
        """
        code = getattr(self.coordinator, "unknown_codes", {}).get(self._description.key)
        return {"kod": code} if code is not None else None


class CtcDisplaySensor(CoordinatorEntity, SensorEntity):
    """A reading harvested from the display's own web interface.

    These come from a page the integration has to navigate to, so they update on
    the slow interval rather than continuously.
    """

    _attr_has_entity_name = True

    def __init__(self, runtime, page_title: str, value: SlowValue) -> None:
        super().__init__(runtime.web)
        self._value = value
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{value.key}"
        self._attr_name = f"{page_title}: {value.label}" if page_title else value.label
        self._attr_device_info = runtime.device
        unit = value.unit
        self._attr_native_unit_of_measurement = UNITS.get(unit or "", unit)
        device_class = DEVICE_CLASSES.get(device_class_for(unit) or "")
        if device_class is not None:
            self._attr_device_class = device_class
        state_class = display_state_class(unit, value.label)
        if state_class == "total_increasing":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        return (self.coordinator.data or {}).get(self._value.key)

    @property
    def available(self) -> bool:
        return (
            self.coordinator.last_update_success
            and self._value.key in (self.coordinator.data or {})
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        return {
            "källa": "displayens webbgränssnitt",
            "sida": str(self._value.page),
            "skärm": str(self._value.screen),
        }


class CtcIdentitySensor(SensorEntity):
    """Something the unit says about itself and then never changes.

    Read from the display once at setup and stored with the entry, so it costs
    nothing to keep and survives the display being unreachable.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, runtime, key: str, name: str, value: str, icon: str) -> None:
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{key}"
        self._attr_name = name
        self._attr_native_value = value
        self._attr_icon = icon
        self._attr_device_info = runtime.device


class CtcCopSensor(CoordinatorEntity, SensorEntity):
    """Delivered heat against supplied energy.

    Modbus knows what went in but never what came out, so this exists only
    where the display's history page is harvested.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:gauge"
    _attr_suggested_display_precision = 2
    # A figure to be looked at over a month, which needs Home Assistant to keep
    # long term statistics for it. Without this the page's graph of the daily
    # coefficient of performance has nothing to draw.
    _attr_state_class = SensorStateClass.MEASUREMENT

    NAMES = {
        "day": "Dygnsvärmefaktor",
        "year": "Årsvärmefaktor",
        "first_year": "Värmefaktor, första året",
        "lifetime": "Värmefaktor, hela livslängden",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_cop_{span}"
        self._attr_name = self.NAMES[span]
        self._attr_device_info = runtime.device

    def _result(self):
        out, consumed = current_totals(self._runtime)
        if self._span == "day":
            return self._runtime.cop.result_day(out, consumed)
        if self._span == "first_year":
            return self._runtime.cop.result_first_year()
        return self._runtime.cop.result(out, consumed)

    @property
    def native_value(self) -> float | None:
        if self._span == "lifetime":
            # The whole life, whatever the rolling window has to say: the tracker
            # answers with the yearly figure as soon as it has one.
            return lifetime_ratio(*current_totals(self._runtime))
        result = self._result()
        # Reporting one span's figure under another span's name would be a
        # different number wearing the wrong label.
        return result.value if result.basis == self._span else None

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        result = self._result()
        attributes = result.as_attributes()
        attributes["tillförd energi ur"] = (
            "displayen" if self._runtime.energy_in is not None else "Modbus 62341"
        )
        if self.native_value is None:
            # An empty figure that says nothing is the thing people ask about.
            out, consumed = current_totals(self._runtime)
            basis = "lifetime" if self._span == "lifetime" else result.basis
            reason = cop_reason(
                None,
                basis,
                out if self._span == "lifetime" else result.energy_out,
                consumed if self._span == "lifetime" else result.energy_in,
                powered_on_hours(self._runtime),
                # Only the Modbus route can be waiting on a register; the
                # display's own counter is a row that was recognised.
                modbus_answered=self._runtime.energy_in is not None
                or modbus_consumption_answered(self._runtime.modbus.answered),
            )
            if reason:
                attributes["skäl"] = reason
        return attributes

    @property
    def available(self) -> bool:
        # Available without a figure, on purpose: an unavailable entity shows no
        # attributes, and the attributes are where the reason for the empty
        # figure is written.
        return self.coordinator.last_update_success
