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

The reread_menu action (R76) reads the same menu. A catch-up whose pause ends
while the action walks queues on the panel lock, and the lock is fair, so it
used to get the panel before the action's write and sweep the whole menu a
second time (F1.1). Here it finds the menu read and walks nothing, and one
whose pause ends after the action's reading waits a pause from that reading.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_catch_up.py
"""

from __future__ import annotations

import asyncio
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
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
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

#: Where the reread_menu action's walk is patched in: catalogue's own, which
#: async_rescan_pages calls under the panel lock. The catch-up's walk is the
#: one the shared stubs patch in the package.
ACTION_WALK = f"custom_components.{DOMAIN}.catalogue.async_discover_pages"

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


# ------------------------------- a reading the action made is not made again


def _whole_menu() -> MenuReading:
    return MenuReading(pages=VSH, complete=True, root=20, home_found=True,
                       tile_found=True, root_entered=True)


async def test_a_catch_up_whose_pause_ends_while_the_action_walks_walks_nothing_more(
    hass, stubs, monkeypatch
):
    version = str((await async_get_integration(hass, DOMAIN)).version)
    retry = integration.MENU_READ_RETRY.total_seconds() + 60
    entry = await _set_up_owing_the_menu(hass, IDENTITY)
    await _let_the_background_run(hass)
    # The first background reading missed; the catch-up sleeps out its pause.
    assert stubs.discover.await_count == 1
    assert integration._MENU_TRIES[entry.entry_id] == 1

    events = ["catch-up walk"]

    def catch_up_walk(*args, **kwargs) -> MenuReading:
        events.append("catch-up walk")
        return MenuReading()

    stubs.discover.side_effect = catch_up_walk
    gate = asyncio.Event()

    async def action_walk(client, require_root=False):
        events.append("action walk")
        await gate.wait()
        return _whole_menu()

    update = hass.config_entries.async_update_entry

    def writing(entry_, **kwargs):
        if kwargs.get("options", {}).get(CONF_MENU_VERSION) == version:
            events.append("write")
        return update(entry_, **kwargs)

    monkeypatch.setattr(hass.config_entries, "async_update_entry", writing)
    panel = entry.runtime_data.web_client.panel
    with patch(ACTION_WALK, action_walk):
        # Not a task of Home Assistant's, so waiting for the core's tasks does
        # not wait for the action.
        action = asyncio.ensure_future(
            hass.services.async_call(
                DOMAIN, "reread_menu", {"config_entry_id": entry.entry_id}, blocking=True
            )
        )
        await _let_the_background_run(hass)
        assert panel.locked() and events == ["catch-up walk", "action walk"]

        # The pause ends while the action holds the panel: the catch-up counts
        # its try and queues on the lock, ahead of the action's write.
        await _advance(hass, retry)
        assert integration._MENU_TRIES[entry.entry_id] == 2
        assert events == ["catch-up walk", "action walk"]

        gate.set()
        await action
        await hass.async_block_till_done()
        await _let_the_background_run(hass)

    # One sweep for the action, none more for the catch-up, and the write
    # before any walk that could follow.
    assert events == ["catch-up walk", "action walk", "write"]
    assert stubs.discover.await_count == 1
    assert entry.options[CONF_MENU_VERSION] == version
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES
    # The try counted for the skipped reading is not given back: the action's
    # reading started the count over.
    assert entry.entry_id not in integration._MENU_TRIES
    assert entry.state is ConfigEntryState.LOADED
    assert len(FakeModbus.instances) == 2, "skrivningen laddar om, en gång"


async def test_a_catch_up_whose_pause_ends_after_the_action_read_waits_a_pause_from_that_reading(
    hass, stubs
):
    retry = integration.MENU_READ_RETRY.total_seconds() + 60
    entry = await _set_up_owing_the_menu(hass, IDENTITY)
    await _let_the_background_run(hass)
    assert stubs.discover.await_count == 1

    # The action reads during the pause and misses: nothing is written, and
    # its reading counts as the last attempt.
    interrupted = MenuReading(pages=VSH[:1], complete=False, home_found=True,
                              tile_found=True, root_entered=True)
    with patch(ACTION_WALK, return_value=interrupted) as action_walk:
        with pytest.raises(HomeAssistantError) as failed:
            await hass.services.async_call(
                DOMAIN, "reread_menu", {"config_entry_id": entry.entry_id}, blocking=True
            )
    assert failed.value.translation_key == "menu_not_read"
    assert action_walk.await_count == 1

    # The pause the catch-up was sleeping ends: it does not walk right after
    # the action's reading, it waits a pause from it.
    await _advance(hass, retry)
    assert stubs.discover.await_count == 1, "ingen vandring direkt efter åtgärdens"
    await _advance(hass, retry)
    assert stubs.discover.await_count == 2
    assert integration._MENU_TRIES[entry.entry_id] == 1
