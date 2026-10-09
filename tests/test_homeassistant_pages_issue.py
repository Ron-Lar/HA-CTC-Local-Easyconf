"""No page read, under a real Home Assistant core: one issue, the text that fits (R12).

An entry whose menu was never read shows menu_unread, one whose menu was read
with nothing ticked shows pages_unticked, and an entry that goes from the one
to the other keeps the same issue with the other text. Shares the stand-ins and
fixtures of test_homeassistant.py and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_pages_issue.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_menu import VSH  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.helpers import issue_registry as ir  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MENU,
    CONF_SLOW_PAGES,
    DOMAIN,
)


def _pages_issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_pages_missing")


async def test_a_menu_never_read_says_so(hass, stubs):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    issue = _pages_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "menu_unread"


async def test_a_menu_read_with_nothing_ticked_says_where_to_tick(hass, stubs):
    entry = await _set_up(hass, **{CONF_MENU: pages_to_storage(VSH), CONF_SLOW_PAGES: []})
    issue = _pages_issue(hass, entry)
    assert issue is not None
    assert issue.translation_key == "pages_unticked"


async def test_the_same_issue_changes_its_text_when_the_menu_turns_up(hass, stubs):
    entry = await _set_up(hass)
    assert _pages_issue(hass, entry).translation_key == "menu_unread"

    # The menu is read and stored with nothing ticked, as a form saved with
    # every box cleared leaves it; the write reloads the entry.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MENU: pages_to_storage(VSH), CONF_SLOW_PAGES: []}
    )
    await hass.async_block_till_done()

    issues = [
        issue
        for (domain, issue_id), issue in ir.async_get(hass).issues.items()
        if domain == DOMAIN and issue_id.startswith(entry.entry_id) and "pages" in issue_id
    ]
    assert [issue.issue_id for issue in issues] == [f"{entry.entry_id}_pages_missing"]
    assert issues[0].translation_key == "pages_unticked"
