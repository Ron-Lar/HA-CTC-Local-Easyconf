"""The diagnostics download: how the integration sees the heat pump, for a bug report.

A thin shell. What goes in is built by diagnostics_data.build, free of Home
Assistant and tested on its own; here the package's books of this run are
handed to it, Home Assistant redacts the keys that name a machine or a
network, and every string that is left is washed of addresses, the MAC
address and the serial number's sequence group, which also turn up in the
title, in the device's identifiers and in the display client's messages.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import _MENU_OUTCOME, _MENU_TRIES, _SWEPT, _WALKED, CtcConfigEntry
from .diagnostics_data import TO_REDACT, build, secrets_for, wash


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CtcConfigEntry
) -> dict[str, Any]:
    """Everything a report needs, for a loaded entry and for one still waiting to start."""
    runtime = getattr(entry, "runtime_data", None)
    found = build(
        data=entry.data,
        options=entry.options,
        runtime=runtime,
        menu_tries=_MENU_TRIES.get(entry.entry_id, 0),
        walked=entry.entry_id in _WALKED,
        swept=entry.entry_id in _SWEPT,
        menu_outcome=_MENU_OUTCOME.get(entry.entry_id),
    )
    # The title carries the address; given only to show it is there.
    found["entry"]["title"] = entry.title
    return wash(
        async_redact_data(found, TO_REDACT),
        secrets_for(entry.data, entry.options, runtime),
    )
