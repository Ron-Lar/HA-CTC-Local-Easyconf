"""The harvest's skip rule under a real Home Assistant core, with one page selected (R8).

An entry with a single harvested page used to walk the panel every round
whoever stood at it: the check that somebody had moved it only ran with more
than one page. Here the entry is set up with one page, the panel is moved by
hand between rounds, and the rounds are driven by the coordinator's own timer:
the round after the move gives way, the one after that too, and the third goes
ahead and puts the panel back on the page it was found on. The display sensor
keeps its value through the skipped rounds. The rule in detail is in
test_harvest_skips.py; this holds it in the real scheduling path.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_skips.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakePanel,
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_menu import menu_page  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402
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
    HARVEST_SKIP_LIMIT,
)

HOME = 20
HISTORY = 25
OTHER = 50
INTERVAL = 1800


class MovablePanel(FakePanel):
    """A display whose panel the test moves by hand, and whose moves it can read."""

    instances: list["MovablePanel"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.page = HOME
        self.moves: list[int] = []
        MovablePanel.instances.append(self)

    async def async_current_page(self) -> int:
        return self.page

    async def async_goto_page(self, target: int, route=None) -> bool:
        if self.page != target:
            self.page = target
            self.moves.append(target)
        return True

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        return await self.async_goto_page(target)

    async def async_goto_home(self, hops: int = 6) -> int | None:
        await self.async_goto_page(HOME)
        return HOME

    async def async_vars(self, screen: int) -> list:
        return [72] if screen == HISTORY * 10 else []


async def _set_up_with_one_page(hass) -> MockConfigEntry:
    stored = pages_to_storage([menu_page(HISTORY, "Historik")])
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={
            CONF_HOST: HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={
            CONF_MENU_VERSION: version,
            CONF_IDENTITY: IDENTITY,
            CONF_MENU: stored,
            CONF_SLOW_PAGES: stored,
            CONF_SLOW_INTERVAL: INTERVAL,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_one_page_gives_way_twice_and_then_harvests_anyway(hass, stubs):
    MovablePanel.instances.clear()
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", MovablePanel):
        entry = await _set_up_with_one_page(hass)
        assert entry.state is ConfigEntryState.LOADED
        (panel,) = MovablePanel.instances
        web = entry.runtime_data.web
        # The first harvest is planned a moment after set-up (R6), not run
        # inside it, and the rows get their entities from it (L3): fire it,
        # then there and back.
        await _advance(hass, MIN_FIRST_DELAY + 1)
        assert panel.moves == [HISTORY, HOME]
        sensor = _entity_id(hass, "sensor", f"p{HISTORY}_utetemperatur")
        assert hass.states.get(sensor).state == "7.2"
        assert web.skips_in_a_row == 0
        assert web.next_attempt is not None

        # Somebody opens another page. Each round that finds the panel there
        # gives way, and the sensor keeps the value it has.
        panel.page = OTHER
        for skipped in range(1, HARVEST_SKIP_LIMIT + 1):
            await _advance(hass, INTERVAL + 1)
            assert panel.moves == [HISTORY, HOME], "panelen rörs inte under någons händer"
            assert web.skips_in_a_row == skipped
            assert web.last_skip_reason == "panelen används av någon annan"
            assert hass.states.get(sensor).state == "7.2"

        # The limit is reached: harvested, and put back where it was found.
        await _advance(hass, INTERVAL + 1)
        assert panel.moves == [HISTORY, HOME, HISTORY, OTHER]
        assert panel.page == OTHER
        assert web.skips_in_a_row == 0
        assert web.last_skip_reason is None
        assert hass.states.get(sensor).state == "7.2"
