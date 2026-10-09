"""What every entity platform does before it adds its entities.

Home Assistant reads ``entity_registry_enabled_default`` only when an entity is
first registered. Before 0.14.0 this integration created a good part of its
entities switched off, the less certain registers, display rows without a unit,
the identity rows and the controls, and from 0.14.0 on it creates every one of
them switched on. An installation from before that keeps them off for good: the
registry remembers the old default, and nothing says why the CTC page is
thinner than the README promises. The two houses had theirs switched on by hand
over the websocket. Here each platform does it itself, for the entities it is
about to add, before adding them: an entity this integration switched off and
now wants on is switched on, and one somebody switched off by hand is left
alone. Home Assistant answers a change of ``disabled_by`` by reloading the
entry about thirty seconds later, once, which the Modbus client's settle time
after a closed socket is made for.

No base class and no timer: the platforms call one function, and only for what
they add, so an entity this version no longer provides is never switched on to
stand in the registry unavailable.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

_LOGGER = logging.getLogger(__name__)


def async_switch_on_new_defaults(hass: Any, entry: Any, platform: str, entities: Iterable[Any]) -> int:
    """Switch on what this integration once switched off and now creates switched on.

    ``entities`` are the ones the platform is about to add. One whose registry
    entry was switched off by the integration, and whose default now is on, is
    switched on before it is added, so it comes up in this very set-up. One
    switched off by the user, or by anything but the integration, is left as
    it is. Returns how many were switched on; the log says it once, on
    warning, since Home Assistant does not show an integration's info lines
    by default and the reload that follows should not come as a surprise.
    """
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entries = er.async_entries_for_config_entry(registry, entry.entry_id)
    if not entries:
        return 0
    integration = er.RegistryEntryDisabler.INTEGRATION
    switched_off = {
        item.unique_id: item.entity_id
        for item in entries
        if item.domain == platform and item.disabled_by == integration
    }
    if not switched_off:
        return 0
    switched_on = 0
    for entity in entities:
        if not entity.entity_registry_enabled_default:
            continue
        entity_id = switched_off.get(entity.unique_id)
        if entity_id is None:
            continue
        registry.async_update_entity(entity_id, disabled_by=None)
        switched_on += 1
    if switched_on:
        _LOGGER.warning(
            "Switched on %d %s entities that an earlier version of the integration "
            "created switched off; entities switched off by hand are left alone. "
            "Home Assistant reloads the entry in about 30 seconds",
            switched_on,
            platform,
        )
    return switched_on
