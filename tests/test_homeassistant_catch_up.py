"""The catch-up task across a write that reloads nothing, under a real Home Assistant core.

The task used to return after every write of the options, trusting the reload
that follows to start it over. Since R7 a write that changes nothing but the
identity, or the screens it was found on (R9), reloads nothing, so when the
sweep found a screen in the same round as the menu reading missed, the task
ended with the menu still owed its second and third try (R13), and the log
promised a retry that never came. Here the menu reading misses while the
sweep finds the heat pump screen, with the identity complete and with it
incomplete, and the menu has to get its three tries either way, with the one
Modbus client still open; a retry that succeeds still writes the menu and
reloads, once.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_catch_up.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    IDENTITY,
    FakeModbus,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_menu import VSH, VSH_PAGES, _pages_in  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_IDENTITY_SCREENS,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_SLOW_PAGES,
    CONF_VISIT_SYSTEM_INFO,
    DOMAIN,
)
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

READ_IDENTITY = f"custom_components.{DOMAIN}.async_read_identity"

#: The heat pump's screen on an i255, made up: the sweep finds it by the firmware date.
HEATPUMP_SCREEN = 138


def _incomplete() -> dict:
    """An identity still short of the heat pump's model and firmware."""
    return {key: value for key, value in IDENTITY.items() if not key.startswith("heatpump")}


async def _finding_the_screen(client, screens=None, sweep=True, need_system=True, need_heatpump=True):
    """The sweep finds the heat pump screen, but the display has not written anything into it yet."""
    if screens is not None and need_heatpump and screens.heatpump is None:
        screens.heatpump = HEATPUMP_SCREEN
    return Identity()


async def _set_up_owing_the_menu(hass, identity: dict, **options):
    """An entry a version behind, with the display refusing the menu and the walk switched off."""
    return await _set_up(
        hass,
        **{
            CONF_MENU_VERSION: "0.9.0",
            CONF_IDENTITY: identity,
            CONF_VISIT_SYSTEM_INFO: False,
            **options,
        },
    )


# ------------------------------------------------ the tries survive the write


@pytest.mark.parametrize("complete", [True, False], ids=["complete identity", "incomplete identity"])
async def test_a_missed_menu_reading_keeps_its_tries_when_a_screen_is_found_beside_it(
    hass, stubs, caplog, complete
):
    retry = integration.MENU_READ_RETRY.total_seconds() + 60
    with patch(READ_IDENTITY, _finding_the_screen):
        entry = await _set_up_owing_the_menu(hass, IDENTITY if complete else _incomplete())
        await _let_the_background_run(hass)
        assert entry.state is ConfigEntryState.LOADED

        # The first round: the menu missed, and the sweep ran and the screen it
        # found was written to the options. With a complete identity too since
        # R21: the identity is read at every start, so a firmware update of the
        # display reaches the device, and the screens it is read from have to
        # be known for that.
        assert stubs.discover.await_count == 1
        assert integration._MENU_TRIES[entry.entry_id] == 1
        assert entry.options[CONF_IDENTITY_SCREENS] == {"heatpump": HEATPUMP_SCREEN}
        assert len(FakeModbus.instances) == 1, "skärmarna laddar inte om"

        # The second and third try come as promised, in this run, without a reload.
        for tries in (2, 3):
            await _advance(hass, retry)
            assert stubs.discover.await_count == tries, f"försök {tries} uteblev"
            assert integration._MENU_TRIES[entry.entry_id] == tries
            assert len(FakeModbus.instances) == 1
        assert entry.options[CONF_MENU_VERSION] == "0.9.0", "en missad läsning stämplar ingen version"
        assert "in 3 attempts" in caplog.text, "och de förbrukade försöken sägs högt"

        # And not a fourth: the panel is a physical thing.
        await _advance(hass, retry)
        assert stubs.discover.await_count == 3
        assert entry.options[CONF_IDENTITY_SCREENS] == {"heatpump": HEATPUMP_SCREEN}


# --------------------------------------------- a retry that works still reloads


async def test_the_retry_after_the_screen_was_written_still_writes_the_menu_and_reloads_once(
    hass, stubs
):
    version = str((await async_get_integration(hass, DOMAIN)).version)
    with patch(READ_IDENTITY, _finding_the_screen):
        entry = await _set_up_owing_the_menu(hass, _incomplete())
        await _let_the_background_run(hass)
        assert entry.options[CONF_IDENTITY_SCREENS] == {"heatpump": HEATPUMP_SCREEN}
        assert len(FakeModbus.instances) == 1

        # Five minutes on the display gives the whole menu: written, stamped, reloaded.
        stubs.discover.return_value = MenuReading(pages=VSH, complete=True)
        await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
        await hass.async_block_till_done()
        await _let_the_background_run(hass)

    assert stubs.discover.await_count == 2
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES
    assert _pages_in(entry, CONF_SLOW_PAGES) == VSH_PAGES, "första läsningen kryssar i allt"
    assert entry.options[CONF_IDENTITY_SCREENS] == {"heatpump": HEATPUMP_SCREEN}, "skärmen står kvar"
    assert len(FakeModbus.instances) == 2, "menyn laddar om, en gång"
    assert entry.state is ConfigEntryState.LOADED
