"""A reread with a gap changes nothing in the registry, under a real Home Assistant core (F9.3, F5.1).

The sweep now leaves a page it could not read whole out of its reading and
calls the reading incomplete (test_menu_gaps.py holds that, on the real i255
history page). Here the background reread after an upgrade gets such a
reading: the stored menu, the tick boxes, the version and the registry
entries of the page with the gap all stand, and the whole reading that
follows is folded in with the page still there.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_menu_gaps.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_menu import VSH, VSH_PAGES, _pages_in  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import MenuReading, pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
)

HISTORY = 25
ROW = f"{DOMAIN}_{HOST}_p{HISTORY}_utetemperatur"


def _row_entity(hass) -> str | None:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, ROW)


async def test_a_reread_with_a_gap_leaves_the_pages_rows_in_the_registry(hass, stubs):
    # An installation from an earlier version: the menu stamped by it, and the
    # history page's row with a registry entry of its own, as every row had
    # before the lazy rows.
    stored = pages_to_storage(VSH)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1, "model": MODEL},
        options={CONF_MENU_VERSION: "0.16.0", CONF_IDENTITY: IDENTITY,
                 CONF_MENU: stored, CONF_SLOW_PAGES: stored},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create("sensor", DOMAIN, ROW, config_entry=entry)

    # The history page's screen timed out during the sweep: the page is left
    # out, named as a gap, and the reading is not the whole menu.
    stubs.discover.return_value = MenuReading(
        pages=[page for page in VSH if page.page != HISTORY], complete=False, gaps=[HISTORY]
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _row_entity(hass) is not None, "uppsättningen städar inte en rad som står på sin sida"
    await _let_the_background_run(hass)

    assert stubs.discover.await_count == 1
    assert integration._MENU_TRIES[entry.entry_id] == 1
    assert entry.options[CONF_MENU_VERSION] == "0.16.0", "en läsning med lucka stämplar ingen version"
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES, "den lagrade menyn står"
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES
    assert _row_entity(hass) is not None, "historiksidans rad har sin registerpost kvar"

    # The next reading is whole: folded in, with the page where it was.
    version = str((await async_get_integration(hass, DOMAIN)).version)
    stubs.discover.return_value = MenuReading(pages=VSH, complete=True)
    await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
    await hass.async_block_till_done()

    assert stubs.discover.await_count == 2
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES
    assert _row_entity(hass) is not None
    assert entry.state is ConfigEntryState.LOADED
