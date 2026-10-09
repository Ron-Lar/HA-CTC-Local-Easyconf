"""The menu re-read under a real Home Assistant core: an interrupted sweep changes nothing.

The re-read after an update used to take any non empty list for the whole
menu: an interrupted sweep was written over the stored menu and stamped with
the version, and the pages it had not reached were gone until the next
release. The ordinary suite can only read the decision as source; here the
background task runs it, with VSH's seven pages in the options.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_menu.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import (  # noqa: E402
    MenuReading,
    pages_from_storage,
    pages_to_storage,
)
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_SLOW_PAGES,
    DOMAIN,
    SlowPage,
    SlowValue,
)


def menu_page(number: int, title: str) -> SlowPage:
    """One operation data page with a reading on it, as the sweep records it."""
    return SlowPage(
        page=number,
        title=title,
        screens=[number * 10],
        values=[
            SlowValue(
                key=f"p{number}_utetemperatur",
                label="Utetemperatur",
                page=number,
                screen=number * 10,
                fmt="%.1f°C",
                var_indices=[0],
                unit="°C",
                scale=0.1,
            )
        ],
        route=[] if number == 20 else [(80 + (number - 21) * 160, 255)],
    )


#: VSH's seven pages: the operation data root and six behind the tab strip.
VSH = [
    menu_page(20, "Driftinfo"),
    menu_page(21, "Värmesystem"),
    menu_page(22, "Värmepump"),
    menu_page(23, "Varmvatten"),
    menu_page(24, "Solpaneler"),
    menu_page(25, "Historik"),
    menu_page(26, "Larmhistorik"),
]
VSH_PAGES = [page.page for page in VSH]


def _pages_in(entry, key: str) -> list[int]:
    return [page.page for page in pages_from_storage(entry.options.get(key))]


# ----------------------------------------------- the re-read after an update


async def test_an_interrupted_rereading_keeps_the_stored_menu_and_its_stamp(hass, stubs):
    """VSH's seven pages stand when the sweep after an upgrade is cut short.

    The sweep comes back with four of the seven and says it is not complete.
    Nothing is written: not the menu, not the tick boxes, not the version, so
    the reading stays owed and is tried again a few minutes on. When that one
    is whole, it is folded in, a page the newer parser found included.
    """
    stored = pages_to_storage(VSH)
    stubs.discover.return_value = MenuReading(pages=VSH[:4], complete=False)
    entry = await _set_up(
        hass, **{CONF_MENU_VERSION: "0.9.0", CONF_MENU: stored, CONF_SLOW_PAGES: stored}
    )
    assert entry.state is ConfigEntryState.LOADED
    await _let_the_background_run(hass)

    assert stubs.discover.await_count == 1
    assert integration._MENU_TRIES[entry.entry_id] == 1
    assert entry.options[CONF_MENU_VERSION] == "0.9.0", "en halv läsning stämplar ingen version"
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES, "VSH:s sju sidor står kvar"
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES, "och alla sju skördas fortfarande"

    # Five minutes on, the display gives the whole menu, with a page this
    # version makes sense of that the old one passed over.
    version = str((await async_get_integration(hass, DOMAIN)).version)
    stubs.discover.return_value = MenuReading(
        pages=VSH + [menu_page(27, "Kompressor")], complete=True
    )
    await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
    await hass.async_block_till_done()

    assert stubs.discover.await_count == 2
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES + [27]
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES + [27], "den nya sidan kryssas i"
    assert entry.state is ConfigEntryState.LOADED
