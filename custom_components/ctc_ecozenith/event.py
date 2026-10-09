"""The heat pump's transitions as events, for the logbook and for automations.

A start, a stop, a defrost, an alarm, a changed SmartGrid mode or system
status: the derived binary sensors show these as states, and an automation
that wants the moment itself has to compare states. Home Assistant has an
entity for the moment: the event entity, which the logbook writes out and a
state trigger fires on without a template. One entity per heat pump, with one
event type per kind of transition, fed by the same watch as the sensors
(transitions.py), so a transition is judged once, on the controller's codes.

The watch keeps a short log with a running number. The entity remembers the
number it has fired up to and fires what came after, so a round that holds two
transitions (an alarm that clears straight into a start) gives two events in
order, and the baseline round after a start of Home Assistant gives none.
Nothing of this goes to the anonymous report.

The entity stays available when a Modbus round fails. Its state is the moment
of the last event, a thing that happened, which a lost line does not undo, and
Home Assistant's own event platform treats it the same way: it restores the
last event across a restart without asking the device. With the coordinator's
availability instead, one lost round took the entity to unavailable and the
next round wrote the old event back, and a state trigger, the plain kind the
README recommends and the only kind on Home Assistant 2024.12, fired the same
alarm a second time. The Modbus sensors say when the line is down.
"""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import CtcConfigEntry
from .keys import unique_id
from .transitions import EVENT_TYPES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: CtcConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if runtime.transitions is not None:
        async_add_entities([CtcEvents(runtime)])


class CtcEvents(CoordinatorEntity, EventEntity):
    """One event per transition the watch finds, after every Modbus round."""

    _attr_has_entity_name = True
    _attr_name = "Händelser"
    #: The event types' texts live under this key in strings.json.
    _attr_translation_key = "events"
    _attr_icon = "mdi:timeline-text-outline"
    _attr_event_types = list(EVENT_TYPES)

    def __init__(self, runtime) -> None:
        super().__init__(runtime.modbus)
        self._watch = runtime.transitions
        #: How far into the watch's log this entity has fired. Starts where
        #: the watch stands, so nothing from before the entity existed fires.
        self._fired = self._watch.seq
        self._attr_unique_id = unique_id(runtime.device, "events")
        self._attr_device_info = runtime.device

    @property
    def available(self) -> bool:
        # The last event is a moment in the past; a round that failed does not
        # make it unhappen, and taking the entity to unavailable and back made
        # a state trigger fire the old event again. See the module docstring.
        return True

    @callback
    def _handle_coordinator_update(self) -> None:
        # The watch has seen this round already: it listens to the coordinator
        # from before the platforms were set up. One state write per event, so
        # each is a state change of its own, in the order they happened. A
        # round with nothing new writes nothing: the state is the last event.
        for transition in self._watch.since(self._fired):
            self._fired = transition.seq
            self._trigger_event(transition.kind, transition.attributes())
            self.async_write_ha_state()
