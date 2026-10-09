"""The first start after an update, under a real Home Assistant core: one menu, one walk.

An installation coming from a release without the display store starts with
the menu owed and nothing stored: the menu is read in the background, which
ends in a write of the options and a reload, and the first harvest is armed a
few seconds after set-up. The harvest comes due while the menu is being read
and queues on the panel lock; the lock is fair, so it walks before the write.
Its store is then written out before the reload rather than after its delay,
so the entry the reload brings comes up on it and owes the next harvest an
interval later. Without that the new entry found nothing, walked every page
again at once, and the panel paid for a whole harvest more.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
display of test_homeassistant_harvest.py, and runs the same way, from a
virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_first_start.py
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_harvest import (  # noqa: E402,F401  (live_panel is autouse)
    INTERVAL,
    LivePanel,
    _display_key,
    _fire,
    live_panel,
)
from test_homeassistant_menu import VSH, VSH_PAGES, _pages_in, menu_page  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ctc_ecozenith.catalogue import MenuReading, pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402

#: The page the newer parser makes sense of, found by the re-read.
NEW_PAGE = menu_page(27, "Kompressor")


def _entry_owing_the_menu() -> MockConfigEntry:
    """VSH's seven pages, identity complete, menu read by an older version. Not yet added."""
    stored = pages_to_storage(VSH)
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1, "model": MODEL},
        options={
            CONF_MENU_VERSION: "0.9.0",
            CONF_IDENTITY: IDENTITY,
            CONF_MENU: stored,
            CONF_SLOW_PAGES: stored,
            CONF_SLOW_INTERVAL: INTERVAL,
        },
    )


async def _settle(hass) -> None:
    """Let the walk, the write and the reload run their course."""
    for _ in range(300):
        await asyncio.sleep(0)
    await hass.async_block_till_done()
    for _ in range(300):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


async def test_the_first_start_after_an_update_walks_the_menu_and_the_pages_once(
    hass, hass_storage, stubs
):
    version = str((await async_get_integration(hass, DOMAIN)).version)
    # The menu reading holds the panel until the test lets it finish, as a
    # real one does for a minute or two.
    reading = asyncio.Event()
    finish = asyncio.Event()

    async def read_the_menu(client, require_root=True) -> MenuReading:
        reading.set()
        await finish.wait()
        return MenuReading(pages=VSH + [NEW_PAGE], complete=True)

    stubs.discover.side_effect = read_the_menu
    entry = _entry_owing_the_menu()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert reading.is_set(), "menyläsningen är i gång"
    (panel,) = LivePanel.instances
    assert panel.visits == [], "uppsättningen rör inte panelen"
    assert _display_key(entry) not in hass_storage

    # The first harvest comes due while the menu is read: it queues on the lock.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=MIN_FIRST_DELAY + 1))
    await _let_the_background_run(hass)
    assert panel.panel.locked(), "menyläsningen håller panelen"
    assert panel.visits == [], "skörden väntar"

    # The menu reading ends: the harvest walks, is written out, and the menu
    # is written, which reloads the entry.
    finish.set()
    await _settle(hass)

    assert panel.walked == VSH_PAGES, "skörden gick klart före omladdningen, en gång, de gamla sidorna"
    assert entry.state is ConfigEntryState.LOADED
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES + [27], "den nya sidan kryssas i"
    assert len(LivePanel.instances) == 2, "menyn laddade om"
    old, new = LivePanel.instances
    assert old is panel

    # The store was there for the set-up the reload brought.
    stored = hass_storage[_display_key(entry)]["data"]
    assert stored["values"]["p21_utetemperatur"] == 7.2
    web = entry.runtime_data.web
    assert web.last_harvest is not None, "den nya posten kom upp på skörden"
    assert web.next_attempt is not None
    owed = (web.next_attempt - web.last_harvest).total_seconds()
    assert abs(owed - INTERVAL) < 60, f"nästa skörd ett intervall efter den förra, inte om {owed} s"

    # So a few seconds on, nothing: the panel is not walked a second time.
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert new.visits == [], "ingen andra vandring efter omladdningen"
    assert old.walked == VSH_PAGES

    # When the next harvest is owed it walks, the new page included.
    await _fire(hass, INTERVAL + 60)
    assert new.walked == VSH_PAGES + [27]
