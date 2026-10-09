""""Read the menu again" during the first harvest, under a real Home Assistant core.

The options flow borrows the running entry's web client, and with it the one
panel lock, only when the runtime is already on the entry. Home Assistant
takes runtime_data away on unload and the set-up used to put it back only
after the first harvest, so a dialog open across a reload could send its
rescan into that window, get a client of its own with a free lock, and walk
the panel the harvest was walking. Since R6 the set-up walks nothing and the
first harvest follows a few seconds later on the coordinator's own clock.
Here that first harvest is held in place and the rescan is sent in the middle
of it; it has to be answered "the panel is in use", with no second client
built.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_rescan.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakePanel,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_menu import VSH  # noqa: E402

from datetime import timedelta  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
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
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402
from custom_components.ctc_ecozenith.web_api import CtcWebError  # noqa: E402


class BlockingPanel(FakePanel):
    """A display whose first answer waits for the test: the harvest holds the panel meanwhile."""

    gate: asyncio.Event | None = None
    instances: list["BlockingPanel"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        BlockingPanel.instances.append(self)

    async def async_current_page(self) -> int:
        if BlockingPanel.gate is not None:
            await BlockingPanel.gate.wait()
        raise CtcWebError("async_current_page: no display in this test")


async def test_read_the_menu_again_gives_way_to_the_first_harvest(hass, stubs):
    """The panel lock is one lock, from the first harvest on.

    The flow must find the entry's own client on the entry, see its lock
    taken, and answer "the panel is in use" rather than build a client of its
    own and walk. Before the fix this very test built that second client: a
    real one, which the plugin caught trying to open a socket.
    """
    BlockingPanel.gate = asyncio.Event()
    BlockingPanel.instances.clear()
    stored = pages_to_storage(VSH)
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
        },
    )
    entry.add_to_hass(hass)

    try:
        with patch(f"custom_components.{DOMAIN}.CtcWebClient", BlockingPanel):
            # The set-up itself walks nothing: it is done before the panel is
            # touched, with the runtime and its client on the entry.
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            assert entry.state is ConfigEntryState.LOADED
            (panel,) = BlockingPanel.instances
            assert not panel.panel.locked(), "uppsättningen rör inte panelen"

            # The first harvest is owed a few seconds on, on the coordinator's
            # own clock, and here it is held in place at its first question.
            async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=MIN_FIRST_DELAY + 1))
            for _ in range(200):
                await asyncio.sleep(0)
                if panel.panel.locked():
                    break
            assert panel.panel.locked(), "skörden håller panelen"

            # The dialog, open since before the reload, sends "read the menu again".
            flow = await hass.config_entries.options.async_init(entry.entry_id)
            assert flow["type"] is FlowResultType.FORM
            result = await hass.config_entries.options.async_configure(
                flow["flow_id"], {"rescan": True}
            )
            assert result["type"] is FlowResultType.ABORT
            assert result["reason"] == "panel_busy"
            # Nobody walked: the harvest still holds the one lock, and no second
            # client was built for the display.
            assert panel.panel.locked()
            assert len(BlockingPanel.instances) == 1

            BlockingPanel.gate.set()
            for _ in range(200):
                await asyncio.sleep(0)
                if not panel.panel.locked():
                    break
            await hass.async_block_till_done()
    finally:
        BlockingPanel.gate = None
    assert entry.state is ConfigEntryState.LOADED
