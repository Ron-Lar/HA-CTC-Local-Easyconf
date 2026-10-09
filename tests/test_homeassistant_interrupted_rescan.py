"""A page only an interrupted reading found, under a real Home Assistant core (R13).

"Read the menu again" offers what an interrupted reading found beside the
stored pages it did not reach, and a page the newer parser found can be
ticked there. The save used to write the tick but not the menu, so the first
form, which builds its tick boxes from the stored menu alone, could neither
show the page nor validate the tick: the form was either refused outright
or, where the browser dropped the unknown value, saved without the page, and
the harvest lost it without a word. Now the folded menu is written too,
unstamped, so the page stays on the list with its tick, the save goes
through, and the whole reading that follows keeps it.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_interrupted_rescan.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol  # noqa: E402  (comes with Home Assistant)

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_menu import VSH, VSH_PAGES, _pages_in, menu_page  # noqa: E402
from test_homeassistant_options import RESCAN, _defaults  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import MenuReading, pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    DOMAIN,
)

NEW_PAGE = menu_page(27, "Kompressor")
TICKED = [str(page) for page in VSH_PAGES]


def _options_of(schema: vol.Schema, field: str) -> list[str]:
    """The values a select field of a form offers."""
    for key, value in schema.schema.items():
        if str(key.schema) == field:
            return [option["value"] for option in value.config["options"]]
    return []


async def test_a_page_only_an_interrupted_reading_found_keeps_its_tick_across_the_form(hass, stubs):
    stored = pages_to_storage(VSH)
    # A version behind: the background's first try misses, so the reading is owed.
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored, CONF_MENU_VERSION: "0.9.0"})
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED
    version = str((await async_get_integration(hass, DOMAIN)).version)

    # The reading from the options is cut short after the root and the new page.
    interrupted = MenuReading(pages=[VSH[0], NEW_PAGE], complete=False)
    with patch(RESCAN, AsyncMock(return_value=interrupted)):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True, CONF_SLOW_PAGES: TICKED}
        )
        assert result["type"] is FlowResultType.FORM and result["step_id"] == "rescan"
        assert _options_of(result["data_schema"], CONF_SLOW_PAGES) == TICKED + ["27"], (
            "de sju lagrade sidorna står kvar och den nya erbjuds"
        )
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_SLOW_PAGES: TICKED + ["27"]}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED

    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES + [27], "sidan är ikryssad"
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES + [27], "och menyn känner den"
    assert entry.options[CONF_MENU_VERSION] == "0.9.0", "men ingen version stämplas: läsningen är skyldig"
    assert 27 in [page.page for page in entry.runtime_data.pages], "och den skördas"

    # A week on, the form is opened to change the interval. It offers the page,
    # its own defaults pass its own schema, and the save keeps the tick.
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    schema = flow["data_schema"]
    assert "27" in _options_of(schema, CONF_SLOW_PAGES)
    defaults = _defaults(schema)
    assert "27" in defaults[CONF_SLOW_PAGES]
    schema(defaults)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {**defaults, CONF_SLOW_INTERVAL: 900}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_SLOW_INTERVAL] == 900
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES + [27], "sidan står kvar i urvalet"

    # The whole reading the background still owes replaces the menu, tick kept.
    stubs.discover.return_value = MenuReading(pages=VSH + [NEW_PAGE], complete=True)
    await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
    await hass.async_block_till_done()
    await _let_the_background_run(hass)
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES + [27]
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES + [27]
    assert entry.state is ConfigEntryState.LOADED


async def test_a_missed_reading_from_the_options_still_writes_no_menu(hass, stubs):
    """The other branch stands: nothing found, nothing written but the person's own fields."""
    stored = pages_to_storage(VSH)
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored, CONF_MENU_VERSION: "0.9.0"})
    await _let_the_background_run(hass)
    stored_menu = entry.options[CONF_MENU]
    with patch(RESCAN, AsyncMock(return_value=MenuReading())):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True, CONF_SLOW_PAGES: TICKED}
        )
        assert result["step_id"] == "rescan"
        assert _options_of(result["data_schema"], CONF_SLOW_PAGES) == TICKED, "ur lagret"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_SLOW_PAGES: TICKED[:3]}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
    await _let_the_background_run(hass)
    assert entry.options[CONF_MENU] == stored_menu, "menyn är orörd"
    assert entry.options[CONF_MENU_VERSION] == "0.9.0"
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES[:3]
