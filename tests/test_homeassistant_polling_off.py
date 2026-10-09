"""An entry with polling switched off names no next attempt, under a real Home Assistant core (R34, R6).

Home Assistant runs no schedule for an entry whose polling is switched off,
whatever the coordinator's interval says, and since R6 nothing is harvested
in set-up either, so such an entry never walks the panel on its own. That is
what switching polling off means. The diagnostic sensor used to name a next
attempt all the same, a moment that passed without one; now it names none,
after set-up as after a refresh asked for by hand, which still harvests.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
display of test_homeassistant_harvest.py, and runs the same way, from a
virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_polling_off.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_harvest import (  # noqa: E402,F401  (live_panel is autouse)
    INTERVAL,
    LivePanel,
    _fire,
    live_panel,
)
from test_homeassistant_menu import VSH, VSH_PAGES  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
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

NEXT = "nästa försök"


async def _set_up(hass, **entry_fields) -> tuple[MockConfigEntry, LivePanel]:
    """VSH's seven pages, identity complete, menu read by this version, no store."""
    stored = pages_to_storage(VSH)
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1, "model": MODEL},
        options={
            CONF_MENU_VERSION: version,
            CONF_IDENTITY: IDENTITY,
            CONF_MENU: stored,
            CONF_SLOW_PAGES: stored,
            CONF_SLOW_INTERVAL: INTERVAL,
        },
        **entry_fields,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    (panel,) = LivePanel.instances
    return entry, panel


def _harvest_sensor(hass):
    return hass.states.get(_entity_id(hass, "sensor", "display_harvest"))


async def test_with_polling_off_no_next_attempt_is_named_and_nothing_is_walked(hass, hass_storage, stubs):
    entry, panel = await _set_up(hass, pref_disable_polling=True)
    state = _harvest_sensor(hass)
    assert state.state == "unknown", "ingen skörd än"
    assert state.attributes.get(NEXT) is None, "och inget försök utlovas"

    # Neither a few seconds on nor an interval on: the schedule is off.
    await _fire(hass, MIN_FIRST_DELAY + 60)
    await _fire(hass, INTERVAL + 100)
    assert panel.visits == []
    assert _harvest_sensor(hass).state == "unknown"
    assert _harvest_sensor(hass).attributes.get(NEXT) is None

    # A refresh asked for by hand still harvests, and still promises nothing.
    await entry.runtime_data.web.async_refresh()
    await hass.async_block_till_done()
    assert panel.walked == VSH_PAGES
    state = _harvest_sensor(hass)
    assert state.state != "unknown"
    assert state.attributes.get(NEXT) is None


async def test_with_polling_on_the_next_attempt_is_named(hass, hass_storage, stubs):
    entry, panel = await _set_up(hass)
    assert _harvest_sensor(hass).attributes.get(NEXT) is not None
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert panel.walked == VSH_PAGES
    assert _harvest_sensor(hass).attributes.get(NEXT) is not None
