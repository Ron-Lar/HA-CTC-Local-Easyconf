"""Sensors from both transports."""

from __future__ import annotations

import logging
from typing import Any

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
from .catalogue import display_state_class, pages_from_storage
from .cop import (
    WINDOWS,
    counter_rows,
    current_totals,
    display_silence,
    modbus_consumption_answered,
    powered_on_hours,
    reason_for,
    stale_rows,
)
from .const import (
    CONF_MENU,
    STATUS_UNKNOWN,
    ModbusSensor,
    SlowValue,
    device_class_for,
    identity_signal,
)
from .keys import prefix_of, previous_keys, unique_id
from .transitions import (
    MEAN_RUN_KEY,
    MEAN_RUN_NAME,
    MINUTES_24H_KEY,
    TRANSITION_SENSORS,
    TransitionSensor,
    mean_run_minutes,
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
from .rows import due_rows, split_rows, vanished_display_keys

_LOGGER = logging.getLogger(__name__)

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
        _async_remove_vanished_rows(hass, entry, runtime)
        # A row that reads CTC's marker for a sensor that is not fitted, and
        # never has read as a number, waits for its entity until it does; see
        # rows.py. The first harvest that gives it a number adds it here.
        numeric = runtime.seen.numeric if runtime.seen is not None else set()
        now, pending = split_rows(runtime.pages, runtime.web.data, numeric)
        entities.extend(CtcDisplaySensor(runtime, page.title, value) for page, value in now)
        if pending:
            web = runtime.web
            _LOGGER.info(
                "%d display rows have never read as a number, so their entities wait "
                "until they do: %s",
                len(pending),
                ", ".join(sorted(pending)),
            )

            @callback
            def _add_rows_that_left_a_number() -> None:
                due = due_rows(pending, web.data)
                if not due:
                    return
                new = [pending.pop(key) for key in due]
                _LOGGER.info(
                    "Display rows that read as a number for the first time get their "
                    "entities: %s",
                    ", ".join(due),
                )
                async_add_entities([CtcDisplaySensor(runtime, page.title, value) for page, value in new])

            entry.async_on_unload(web.async_add_listener(_add_rows_that_left_a_number))
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
        for span in ("day", "week", "month", "year", "first_year", "lifetime"):
            entities.append(CtcCopSensor(runtime, span))

    # What the status codes said from one poll to the next: starts, the last
    # run, and where the history page is harvested the mean run over a day.
    if runtime.transitions is not None:
        entities.extend(CtcTransitionSensor(runtime, item) for item in TRANSITION_SENSORS)
        if runtime.web is not None and runtime.starts_per_day is not None:
            entities.append(CtcMeanRunSensor(runtime))
    if runtime.alarms is not None:
        entities.append(CtcLastAlarmSensor(runtime))

    async_add_entities(entities)

    @callback
    def _identity_filled_in() -> None:
        if new := identity_sensors():
            async_add_entities(new)

    entry.async_on_unload(async_dispatcher_connect(hass, signal, _identity_filled_in))


def _async_remove_vanished_rows(hass: HomeAssistant, entry: CtcConfigEntry, runtime) -> None:
    """Take the registry entries away for rows no longer on a page the menu still has.

    The rule is rows.vanished_display_keys: a page the menu does not know, one
    the sweep did not reach, keeps every entry it has, and a row that is still
    on its page is never removed however long it has read the marker. What
    goes is a row the parser folded away or renamed, the i255's two halves of
    the clock row for instance, which otherwise sit in the registry
    unavailable for good. One line in the log per entry, so the owner can see
    what went and why.
    """
    from homeassistant.helpers import entity_registry as er

    menu = pages_from_storage(entry.options.get(CONF_MENU)) + list(runtime.pages)
    prefix = prefix_of(runtime.device)
    registry = er.async_get(hass)
    entries = {
        item.unique_id[len(prefix):]: item
        for item in er.async_entries_for_config_entry(registry, entry.entry_id)
        if item.domain == "sensor" and item.unique_id and item.unique_id.startswith(prefix)
    }
    # A row that has moved to the key of its place (L2) was carried over by
    # set-up before this ran; one that could not be is left, not removed.
    for key in sorted(vanished_display_keys(menu, entries, moved=previous_keys(menu))):
        item = entries[key]
        _LOGGER.info(
            "The display row %s is no longer on its page in the display's menu, so its "
            "entity %s is removed from the registry",
            key,
            item.entity_id,
        )
        registry.async_remove(item.entity_id)


class CtcModbusSensor(CoordinatorEntity, SensorEntity):
    """One documented Modbus register."""

    _attr_has_entity_name = True

    def __init__(self, runtime, description: ModbusSensor) -> None:
        super().__init__(runtime.modbus)
        self._description = description
        self._attr_unique_id = unique_id(runtime.device, description.key)
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
        self._attr_unique_id = unique_id(runtime.device, value.key)
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
        self._attr_unique_id = unique_id(runtime.device, "display_harvest")
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
            "hoppade över i rad": int(getattr(coordinator, "skips_in_a_row", 0) or 0),
            "misslyckade i rad": int(getattr(patience, "failures", 0) or 0),
            "nästa försök": (
                next_attempt.isoformat(timespec="seconds") if next_attempt is not None else None
            ),
            "senaste skäl": reason,
            "sidor lästa": list(getattr(coordinator, "pages_read", []) or []),
            "sidor missade": list(getattr(coordinator, "pages_missed", []) or []),
        }


class CtcLastAlarmSensor(CoordinatorEntity, SensorEntity):
    """The alarm the display showed last, with its E-code, as the panel prints it.

    Read off the pages the harvest visits anyway, so it updates on the slow
    interval and never costs the panel a step. Empty until the panel has
    shown an alarm; afterwards it keeps the latest one, and the attributes say
    whether it is still showing.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:alert-circle-outline"

    def __init__(self, runtime) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, "last_alarm")
        self._attr_name = "Senaste larm"
        self._attr_device_info = runtime.device

    @property
    def native_value(self) -> str | None:
        latest = self._runtime.alarms.latest
        return latest["shown"] if latest else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        latest = self._runtime.alarms.latest
        if latest is None:
            return None
        return {
            "kod": latest["code"],
            "text": latest["text"],
            "start": latest["start"],
            "slut": latest["end"],
            "utetemperatur vid start": latest["outdoor"],
            "pågår": "ja" if latest["end"] is None else "nej",
        }

    @property
    def available(self) -> bool:
        # Available while empty and while the display is quiet, like the
        # harvest sensor and the stored spans of the coefficient of
        # performance: the state and the attributes come out of the alarm
        # log's own store, not out of the harvest's data, and are as true
        # after three missed harvests as before them. Going unavailable then
        # would show an automation a change of state that no alarm made, and
        # take the code, the start and the outdoor temperature out of the
        # history for as long as the display stays quiet. That the display is
        # quiet is already said by Senaste displayskörd and by the reason
        # under the coefficient of performance.
        return True


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
        self._attr_unique_id = unique_id(runtime.device, key)
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


class CtcTransitionSensor(CoordinatorEntity, SensorEntity):
    """Something the heat pump's transitions tell: a start, a count, a run.

    Read off the watch in transitions.py after every Modbus round. The watch is
    fed before the platforms are set up, so what the sensor reads here is the
    round the coordinator has just finished.
    """

    _attr_has_entity_name = True

    def __init__(self, runtime, item: TransitionSensor) -> None:
        super().__init__(runtime.modbus)
        self._watch = runtime.transitions
        self._item = item
        self._attr_unique_id = unique_id(runtime.device, item.key)
        self._attr_name = item.name
        self._attr_device_info = runtime.device
        self._attr_icon = item.icon
        if item.kind == "timestamp":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
        elif item.kind == "minutes":
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            # A count since midnight: a total with a reset, so the long term
            # statistics sum it over the day rather than average it.
            self._attr_state_class = SensorStateClass.TOTAL

    @property
    def native_value(self):
        return self._item.value(self._watch)

    @property
    def last_reset(self):
        if self._item.kind != "count":
            return None
        return self._watch.counting_since

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        return self._item.attributes(self._watch)


class CtcMeanRunSensor(CoordinatorEntity, SensorEntity):
    """The day's compressor minutes divided by the display's starts per day.

    Modbus 62234 is fresh every round; the display's "Antal starter /24 h" is
    harvested on the slow interval, so the figure can lag by up to that. Both
    numbers stand as attributes, with the moment the starts were read. Only
    where the history page is harvested, and only while that row is fresh by
    the coordinator's rule (R5): the data carries the row across a page that
    is not reached and seeds it from the store at a restart, so without the
    rule today's minutes would be divided by a count from days ago and shown
    as the day's figure. The row's own sensor goes unavailable at that point,
    and this figure goes empty with it.
    """

    _attr_has_entity_name = True
    _attr_icon = "mdi:av-timer"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, runtime) -> None:
        super().__init__(runtime.modbus)
        self._runtime = runtime
        self._attr_unique_id = unique_id(runtime.device, MEAN_RUN_KEY)
        self._attr_name = MEAN_RUN_NAME
        self._attr_device_info = runtime.device

    def _parts(self) -> tuple[object, object]:
        minutes = (self.coordinator.data or {}).get(MINUTES_24H_KEY)
        web = self._runtime.web
        key = self._runtime.starts_per_day.key
        starts = None
        if web is not None and web.last_update_success and web.is_fresh(key):
            starts = (web.data or {}).get(key)
        return minutes, starts

    @property
    def native_value(self) -> float | None:
        return mean_run_minutes(*self._parts())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        minutes, starts = self._parts()
        web = self._runtime.web
        read_at = web.last_read(self._runtime.starts_per_day.key) if web is not None else None
        return {
            "kompressordrift senaste dygnet": minutes,
            "antal starter /24 h": starts,
            "antal starter senast läst": (
                read_at.isoformat(timespec="seconds") if read_at is not None else None
            ),
        }


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
        "week": "Veckovärmefaktor",
        "month": "Månadsvärmefaktor",
        "year": "Årsvärmefaktor",
        "first_year": "Värmefaktor, första året",
        "lifetime": "Värmefaktor, hela livslängden",
    }

    def __init__(self, runtime, span: str) -> None:
        super().__init__(runtime.web)
        self._runtime = runtime
        self._span = span
        self._attr_unique_id = unique_id(runtime.device, f"cop_{span}")
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
        if self._span in WINDOWS:
            return tracker.result_window(out, consumed, self._span)
        return tracker.result_year(out, consumed)

    def _silence(self) -> str | None:
        """What a quiet display does to this figure: the notice, or None.

        The first year is stored once and for all, so a display that has gone
        quiet cannot age it. Every other span is a difference against counters
        that are no longer being read, and shows nothing but the notice. The
        counters' own rows go in too: since R5 a page that is not reached is
        no failure of the harvest, so the display answers while the counters
        age, and the notice has to come from the rows themselves.
        """
        if self._span == "first_year":
            return None
        return display_silence(self._runtime.web, *counter_rows(self._runtime))

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
        # quiet takes it with it, as it takes the display's own sensors, and
        # so does a counter row that was read once and has gone stale since,
        # which is when that row's own sensor goes unavailable (R5). A row
        # that has never been read leaves the day standing, with the reason
        # that the counters have not been read. The other spans rest on
        # stored samples and say that the display is quiet.
        if self._span == "day":
            return self.coordinator.last_update_success and not stale_rows(
                self.coordinator, counter_rows(self._runtime)
            )
        return True
