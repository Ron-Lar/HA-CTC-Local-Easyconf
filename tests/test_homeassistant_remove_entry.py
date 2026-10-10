"""Removing an entry under a real Home Assistant core takes all its notices (F5.5).

Each of the five issues an entry can raise is in the registry when the entry is
removed, the notice about the menu raised by the set-up itself, and none of them
is left afterwards; another entry's notice stays. Shares the stand-ins and
fixtures of test_homeassistant.py and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_remove_entry.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.ctc_ecozenith.const import DOMAIN  # noqa: E402

#: What the integration raises besides the notice about the menu, by its key.
OTHERS = ("history_page_missing", "identity_incomplete", "update_available", "modbus_busy")


def _ids(hass, prefix: str) -> list[str]:
    return sorted(
        issue_id
        for (domain, issue_id) in ir.async_get(hass).issues
        if domain == DOMAIN and issue_id.startswith(prefix)
    )


def _raise(hass, issue_id: str, key: str) -> None:
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=key,
    )


async def test_removing_the_entry_takes_every_notice_it_had(hass, stubs):
    entry = await _set_up(hass)
    for key in OTHERS:
        _raise(hass, f"{entry.entry_id}_{key}", key)
    _raise(hass, "another_entry_update_available", "update_available")
    assert len(_ids(hass, entry.entry_id)) == 5, "menyns ärende kommer av uppsättningen"

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert _ids(hass, entry.entry_id) == []
    assert _ids(hass, "another_entry") == ["another_entry_update_available"]
