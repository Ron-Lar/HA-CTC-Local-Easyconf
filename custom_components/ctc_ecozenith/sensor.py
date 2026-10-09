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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from homeassistant.const import EntityCategory

from . import CtcConfigEntry
from .catalogue import display_state_class
from .cop import (
    current_totals,
    display_silence,
    modbus_consumption_answered,
    powered_on_hours,
    reason_for,
)
from .const import (
    DOMAIN,
    STATUS_UNKNOWN,
    ModbusSensor,
    SlowValue,
    device_class_for,
    identity_signal,
)

#: What the unit says about itself, one sensor each: the entity key, the name,
#: where on the identity the value is read, and the icon.
IDENTITY_ROWS = (
    ("hp_model", "Värmepumpsmodell", lambda i: i.heatpump_model, "mdi:heat-pump-outline"),
    ("display_fw", "Programversion display", lambda i: i.display_firmware, "mdi:chip"),
    ("hp_fw", "Programversion VP-styrkort", lambda i: i.heatpump_firmware, "mdi:chip"),
    ("bootloader", "Bootloaderversion", lambda i: i.bootloader, "mdi:chip"),
    ("serial", "Serienummer", lambda i: i.serial, "mdi:identifier"),
    ("made", "Tillverkad", lambda i: i.manufactured, "mdi:factory"),
)

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
        entities.append(CtcHarvestSensor(runtime))

    # What the unit is: static once read, but read late where the system
    # information page had never been shown on the panel. A row gets its
    # sensor when its value is there, at set-up or when the background walk
    # fills the identity in afterwards; a row that has no value yet has no
    # sensor, as before.
    signal = identity_signal(entry.entry_id)
    offered: set[str] = set()

    def identity_sensors() -> list[SensorEntity]:
        new: list[SensorEntity] = []
        for key, name, read, icon in IDENTITY_ROWS:
            if key in offered or not read(runtime.identity):
                continue
            offered.add(key)
            new.append(CtcIdentitySensor(runtime, key, name, read, icon, signal))
        return new

    entities.extend(identity_sensors())

    if runtime.cop is not None:
        for span in ("day", "year", "first_year", "lifetime"):
            entities.append(CtcCopSensor(runtime, span))

    async_add_entities(entities)

    @callback
    def _identity_filled_in() -> None:
        if new := identity_sensors():
            async_add_entities(new)

    entry.async_on_unload(async_dispatcher_connect(hass, signal, _identity_filled_in))


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
        """Available while the reading is fresh, page by page.

        The data carries a value across a page that could not be reached, so
        being in the data is not enough: the value has to have been read off
        the panel recently, within as many intervals as a whole harvest may
        fail in a row. A page that has stopped answering thus goes unavailable
        after the same patience as a display that has, instead of showing
        yesterday's delivered heat as today's for days.
        """
        return self.coordinator.last_update_success and self.coordinator.is_fresh(
            self._value.key
        )

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        attributes = {
            "källa": "displayens webbgränssnitt",
            "sida": str(self._value.page),
            "skärm": str(self._value.screen),
        }
        # When the value was actually read, so a reading carried from an
        # earlier harvest is seen for what it is.
        read_at = self.coordinator.last_read(self._value.key)
        if read_at is not None:
            attributes["senast läst"] = read_at.isoformat(timespec="seconds")
        return attributes


class CtcHarvestSensor(CoordinatorEntity, SensorEntity):
    """When the display was last read, and how the reading of it is going.

    The harvest walks the physical panel, so it runs rarely, gives way to
    whoever stands at the panel and keeps trying quietly when the display is
    slow. All of that was in the log and nowhere else: a value twenty minutes
    old looked like one two days old, and a panel somebody had left on the
    wrong page skipped every harvest without a word on the device page. This
    is the device's own account of it: the moment of the last harvest that
    read a page, and in the attributes how many harvests in a row were skipped
    or failed, when the next attempt is, and why the last one gave nothing.

    Available whatever the display does: a harvest that fails is exactly what
    it is there to show, and an unavailable entity shows no attributes.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-check-outline"
    _attr_name = "Senaste displayskörd"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.web)
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_display_harvest"
        self._attr_device_info = runtime.device

    @property
    def native_value(self):
        return getattr(self.coordinator, "last_harvest", None)

    @property
    def available(self) -> bool:
        return True

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """How the harvesting is going, in words that stand on their own.

        The counters are read with defaults: the skip rule and its tally live
        on the coordinator and have grown in steps, and the account here must
        not break because one of them is not there yet.
        """
        coordinator = self.coordinator
        patience = getattr(coordinator, "patience", None)
        next_attempt = getattr(coordinator, "next_attempt", None)
        reason = getattr(coordinator, "last_skip_reason", None) or getattr(
            coordinator, "last_failure", None
        )
        return {
            "hoppade över i rad": int(getattr(coordinator, "skipped_in_a_row", 0) or 0),
            "misslyckade i rad": int(getattr(patience, "failures", 0) or 0),
            "nästa försök": (
                next_attempt.isoformat(timespec="seconds") if next_attempt is not None else None
            ),
            "senaste skäl": reason,
            "sidor lästa": list(getattr(coordinator, "pages_read", []) or []),
            "sidor missade": list(getattr(coordinator, "pages_missed", []) or []),
        }


class CtcIdentitySensor(SensorEntity):
    """Something the unit says about itself and then never changes.

    Read from the display and stored with the entry, so it costs nothing to
    keep and survives the display being unreachable. Read live off the
    runtime's identity, and written again on the identity signal: a serial
    number or a version that the background walk finds after set-up shows up
    here without a reload of the entry.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False

    def __init__(self, runtime, key: str, name: str, read, icon: str, signal: str) -> None:
        host = next(iter(runtime.device["identifiers"]))[1]
        self._attr_unique_id = f"{DOMAIN}_{host}_{key}"
        self._attr_name = name
        self._attr_icon = icon
        self._attr_device_info = runtime.device
        self._runtime = runtime
        self._read = read
        self._signal = signal

    @property
    def native_value(self):
        return self._read(self._runtime.identity)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._signal, self.async_write_ha_state)
        )


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
        """The tracker's answer for this span, and nothing but this span."""
        tracker = self._runtime.cop
        out, consumed = current_totals(self._runtime)
        if self._span == "day":
            return tracker.result_day(out, consumed)
        if self._span == "first_year":
            return tracker.result_first_year()
        if self._span == "lifetime":
            return tracker.result_lifetime(out, consumed)
        return tracker.result_year(out, consumed)

    def _silence(self) -> str | None:
        """What a quiet display does to this figure: the notice, or None.

        The first year is stored once and for all, so a display that has gone
        quiet cannot age it. Every other span is a difference against counters
        that are no longer being read, and shows nothing but the notice.
        """
        if self._span == "first_year":
            return None
        return display_silence(self._runtime.web)

    @property
    def native_value(self) -> float | None:
        if self._silence() is not None:
            return None
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
        # An empty figure that says nothing is the thing people ask about.
        out, consumed = current_totals(self._runtime)
        reason = reason_for(
            result,
            out,
            consumed,
            powered_on_hours(self._runtime),
            # Only the Modbus route can be waiting on a register; the
            # display's own counter is a row that was recognised.
            modbus_answered=self._runtime.energy_in is not None
            or modbus_consumption_answered(self._runtime.modbus.answered),
            display=self._silence(),
        )
        if reason:
            attributes["skäl"] = reason
        return attributes

    @property
    def available(self) -> bool:
        # Available without a figure, on purpose: an unavailable entity shows no
        # attributes, and the attributes are where the reason for the empty
        # figure is written. The day is the exception: it is a difference
        # against the counters as they stand now, so a display that has gone
        # quiet takes it with it, as it takes the display's own sensors. The
        # other spans rest on stored samples and say that the display is quiet.
        if self._span == "day":
            return self.coordinator.last_update_success
        return True
