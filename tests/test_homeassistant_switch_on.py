"""What an earlier version switched off, and a firmware update, under a real core (R21).

An installation from before 0.14.0 has entities in its registry that the
integration created switched off, and Home Assistant never reads the default
again. Here the registry holds one sensor and one control switched off by the
integration and one sensor switched off by its owner, and set-up has to switch
on the first two, before adding them, and leave the third alone; Home
Assistant then reloads the entry once, thirty seconds on, and that is the end
of it. The reload cancels the entry's tasks, so the catch-up task holds the
panel until it has come, and a menu reading owed after the update starts in
the set-up the reload brings, not in the one it cuts short (F4.2). The entry's
option to switch off newly added entities is the owner's: Home Assistant then
registers everything as switched off by the integration, and nothing of it is
switched on (F4.1). And the identity: a complete one is read again at start,
and a newer display firmware reaches the device without a reload.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_switch_on.py
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakeModbus,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import (  # noqa: E402
    RELOAD_AFTER_UPDATE_DELAY,
    ConfigEntryState,
)
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import MenuReading  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

#: A display firmware newer than the one the entry has stored, made up.
NEWER_FIRMWARE = "2026-06-10"


def _unique_id(key: str) -> str:
    return f"{DOMAIN}_{HOST}_{key}"


async def _entry(hass, menu_version: str | None = None, **kwargs) -> MockConfigEntry:
    """The entry _set_up makes, added but not yet set up.

    With ``menu_version``, an entry whose menu an earlier version read, so the
    catch-up task owes it a reading; ``kwargs`` go to the entry, such as the
    system options.
    """
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
        options={CONF_MENU_VERSION: menu_version or version, CONF_IDENTITY: IDENTITY},
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


class HangingMenu:
    """A menu reading that goes on until let go, and says when it starts and is cut short."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.go = asyncio.Event()

    async def read(self, client, require_root=True):
        self.events.append("start")
        try:
            await self.go.wait()
        except asyncio.CancelledError:
            self.events.append("cancelled")
            raise
        return MenuReading()


async def test_what_an_earlier_version_switched_off_comes_on_and_the_owners_choice_stands(
    hass, caplog
):
    entry = await _entry(hass)
    registry = er.async_get(hass)
    off = er.RegistryEntryDisabler
    outdoor = registry.async_get_or_create(
        "sensor", DOMAIN, _unique_id("outdoor_temp"), config_entry=entry, disabled_by=off.INTEGRATION
    )
    flow = registry.async_get_or_create(
        "sensor", DOMAIN, _unique_id("hs1_flow"), config_entry=entry, disabled_by=off.USER
    )
    room = registry.async_get_or_create(
        "number", DOMAIN, _unique_id("ctl_room_setpoint_1"), config_entry=entry, disabled_by=off.INTEGRATION
    )

    with caplog.at_level(logging.WARNING):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    # On, and up in this very set-up: the switch comes before the adding.
    assert registry.async_get(outdoor.entity_id).disabled_by is None
    assert hass.states.get(outdoor.entity_id).state == "7.2"
    assert registry.async_get(room.entity_id).disabled_by is None
    assert hass.states.get(room.entity_id) is not None
    # The owner's choice stands.
    assert registry.async_get(flow.entity_id).disabled_by is off.USER
    assert hass.states.get(flow.entity_id) is None
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("Switched on 1 sensor entities" in line for line in warned), warned
    assert any("Switched on 1 number entities" in line for line in warned), warned

    # Home Assistant reloads the entry once, thirty seconds on, and only once:
    # the set-up that follows finds nothing left to switch on.
    assert len(FakeModbus.instances) == 1
    await _advance(hass, 31)
    await hass.async_block_till_done()
    assert len(FakeModbus.instances) == 2
    assert entry.state is ConfigEntryState.LOADED
    await _advance(hass, 31)
    await hass.async_block_till_done()
    assert len(FakeModbus.instances) == 2, "ingen omladdning i en slinga"
    assert registry.async_get(flow.entity_id).disabled_by is off.USER


async def test_the_owners_option_against_new_entities_switches_nothing_on(hass, caplog):
    """With Enable newly added entities off, the registry says integration for all (F4.1)."""
    entry = await _entry(hass, pref_disable_new_entities=True)
    registry = er.async_get(hass)
    off = er.RegistryEntryDisabler
    with caplog.at_level(logging.WARNING):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert entities
    assert {item.disabled_by for item in entities} == {off.INTEGRATION}, "så registrerar HA dem"

    # The next start finds every one of them switched off by the integration,
    # and leaves them so: the owner asked for that.
    with caplog.at_level(logging.WARNING):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    assert len(FakeModbus.instances) == 2
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    assert {item.disabled_by for item in entities} == {off.INTEGRATION}
    assert not [r for r in caplog.records if "Switched on" in r.getMessage()]
    assert entry.runtime_data.switched_on == 0
    await _advance(hass, RELOAD_AFTER_UPDATE_DELAY + 1)
    await hass.async_block_till_done()
    assert len(FakeModbus.instances) == 2, "ingen omladdning"


async def _set_up_from_before_0_14(hass) -> MockConfigEntry:
    """An entry from 0.9.3: the menu owed a reading and one sensor switched off by the integration."""
    entry = await _entry(hass, menu_version="0.9.3")
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        _unique_id("outdoor_temp"),
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.INTEGRATION,
    )
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.switched_on == 1
    return entry


async def test_the_reload_after_switching_on_cuts_no_menu_reading_short(hass, stubs):
    """The panel is held until Home Assistant's reload, and the reading starts after it (F4.2)."""
    menu = HangingMenu()
    stubs.discover.side_effect = menu.read
    entry = await _set_up_from_before_0_14(hass)
    # Nothing begun and nothing counted while the reload is owed.
    assert menu.events == []
    assert entry.entry_id not in integration._MENU_TRIES
    assert entry.runtime_data.web_client.panel.locked(), "panelen hålls, så skörden köar"

    await _advance(hass, RELOAD_AFTER_UPDATE_DELAY + 1)
    await hass.async_block_till_done()
    await _let_the_background_run(hass)
    assert len(FakeModbus.instances) == 2, "Home Assistants omladdning"
    assert entry.runtime_data.switched_on == 0, "inget kvar att slå på"
    assert menu.events == ["start"], "läsningen börjar i uppsättningen efter omladdningen"
    assert integration._MENU_TRIES[entry.entry_id] == 1

    # And nothing comes along to cut it short.
    await _advance(hass, RELOAD_AFTER_UPDATE_DELAY + 1)
    await hass.async_block_till_done()
    assert menu.events == ["start"]
    assert len(FakeModbus.instances) == 2
    assert integration._MENU_TRIES[entry.entry_id] == 1
    menu.go.set()
    await _let_the_background_run(hass)


async def test_a_reload_that_does_not_come_only_puts_the_menu_off(hass, stubs):
    """Held for SWITCH_ON_HOLD and no longer: the reading still comes in this run."""
    menu = HangingMenu()
    stubs.discover.side_effect = menu.read
    hold = integration.SWITCH_ON_HOLD.total_seconds()
    with patch("homeassistant.config_entries.RELOAD_AFTER_UPDATE_DELAY", 10 * hold):
        entry = await _set_up_from_before_0_14(hass)
        await _advance(hass, hold - 5)
        assert menu.events == []
        assert entry.entry_id not in integration._MENU_TRIES
        await _advance(hass, hold + 1)
        await _let_the_background_run(hass)
        assert len(FakeModbus.instances) == 1, "ingen omladdning i det här provet"
        assert menu.events == ["start"]
        assert integration._MENU_TRIES[entry.entry_id] == 1
        menu.go.set()
        await _let_the_background_run(hass)
        assert not entry.runtime_data.web_client.panel.locked(), "hållet släpps"


async def test_the_hold_outlasts_home_assistants_delay_before_its_reload(hass):
    assert integration.SWITCH_ON_HOLD.total_seconds() > RELOAD_AFTER_UPDATE_DELAY + 10


async def test_a_fresh_installation_switches_nothing_on_and_reloads_nothing(hass, caplog):
    with caplog.at_level(logging.WARNING):
        entry = await _set_up(hass)
    assert not [r for r in caplog.records if "Switched on" in r.getMessage()]
    await _advance(hass, 31)
    assert len(FakeModbus.instances) == 1
    assert entry.state is ConfigEntryState.LOADED


async def test_both_coordinators_know_their_entry(hass):
    entry = await _set_up(hass)
    assert entry.runtime_data.modbus.config_entry is entry


async def test_a_newer_display_firmware_reaches_the_device_at_start_without_a_reload(hass):
    """A complete identity is read again, and the newer version wins, in place."""
    newer = AsyncMock(return_value=Identity(display_firmware=NEWER_FIRMWARE))
    with patch(f"custom_components.{DOMAIN}.async_read_identity", newer):
        entry = await _set_up(hass)
        await _let_the_background_run(hass)
    assert newer.await_count == 1, "en komplett identitet läses ändå"
    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.sw_version == NEWER_FIRMWARE
    assert entry.runtime_data.identity.display_firmware == NEWER_FIRMWARE
    assert entry.runtime_data.identity.serial == IDENTITY["serial"], "resten står kvar"
    assert entry.options[CONF_IDENTITY]["display_firmware"] == NEWER_FIRMWARE
    assert len(FakeModbus.instances) == 1, "ingen omladdning för en ny version"


async def test_a_reading_that_finds_the_same_identity_writes_nothing(hass):
    same = AsyncMock(return_value=Identity(display_firmware=IDENTITY["display_firmware"]))
    with patch(f"custom_components.{DOMAIN}.async_read_identity", same):
        entry = await _set_up(hass)
        before = dict(entry.options)
        await _let_the_background_run(hass)
    assert same.await_count == 1
    assert dict(entry.options) == before
    assert len(FakeModbus.instances) == 1
