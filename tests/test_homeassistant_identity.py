"""Identity and release check without a reload, under a real Home Assistant core (R7).

The background walk to the system information page used to write what it
found through the options, and the reload that followed was what carried the
serial number into the device and the sensors. Here the walk finds the serial
number and the entry has to show it everywhere, the device, the sensors, the
repairs view, the runtime and the stored options, with the one Modbus client
still open: no reload. A real change of the options still reloads. And the
release check: a reload asks GitHub nothing, and a GitHub that does not answer
leaves the notice as it was.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_identity.py
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    FakeModbus,
    _advance,
    _entity_id,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.helpers import (  # noqa: E402
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_FAST_INTERVAL,
    CONF_IDENTITY,
    DOMAIN,
)
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

#: What the walk finds on the system information page, made up.
FOUND = Identity(
    serial="7331 2412 0001",
    mac="02:00:00:00:00:01",
    display_firmware="2024-03-01",
    bootloader="1.0",
)


def _issue(hass, entry, key: str):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{entry.entry_id}_{key}")


# ------------------------------------------------------------- the identity


async def test_a_serial_number_found_by_the_walk_reaches_everything_without_a_reload(hass):
    """The identity is adopted in place: device, sensors, repairs view, runtime, options."""
    incomplete = {"heatpump_model": IDENTITY["heatpump_model"], "heatpump_firmware": "2.1"}
    # The walk waits for the test, so the entry can be looked at before and after.
    gate = asyncio.Event()
    walks = 0

    async def walk(*args, **kwargs) -> Identity:
        nonlocal walks
        walks += 1
        await gate.wait()
        return FOUND

    with patch(f"custom_components.{DOMAIN}.async_read_identity_via_panel", walk):
        entry = await _set_up(hass, **{CONF_IDENTITY: incomplete})
        assert entry.state is ConfigEntryState.LOADED
        assert _issue(hass, entry, integration.ISSUE_IDENTITY) is not None
        assert hass.states.get(_entity_id(hass, "sensor", "hp_model")).state == "EcoAir 720M"
        registry = er.async_get(hass)
        assert registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_serial") is None
        gate.set()
        await _let_the_background_run(hass)
        assert walks == 1

    # One client from start to finish: nothing was reloaded.
    assert len(FakeModbus.instances) == 1
    assert entry.state is ConfigEntryState.LOADED

    runtime = entry.runtime_data
    assert runtime.identity.serial == FOUND.serial
    assert runtime.identity.heatpump_model == IDENTITY["heatpump_model"], "det gamla behålls"

    (device,) = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert device.serial_number == FOUND.serial
    assert device.sw_version == FOUND.display_firmware
    assert device.hw_version == FOUND.bootloader
    assert device.model == f"{entry.data['model']} + {IDENTITY['heatpump_model']}"

    # The sensors the serial number brings exist now, with their values, and
    # the one that was there from the start still is.
    assert hass.states.get(_entity_id(hass, "sensor", "serial")).state == FOUND.serial
    assert hass.states.get(_entity_id(hass, "sensor", "made")).state == "2024 vecka 12"
    assert hass.states.get(_entity_id(hass, "sensor", "display_fw")).state == FOUND.display_firmware
    assert hass.states.get(_entity_id(hass, "sensor", "hp_model")).state == "EcoAir 720M"

    assert _issue(hass, entry, integration.ISSUE_IDENTITY) is None, "reparationsvyn följer med"
    assert entry.options[CONF_IDENTITY]["serial"] == FOUND.serial, "nästa start har den"
    assert len(FakeModbus.instances) == 1, "skrivningen av identiteten laddade inte om"

    # Somebody's own change of the options still reloads, as it must.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_FAST_INTERVAL: 45}
    )
    await hass.async_block_till_done()
    assert len(FakeModbus.instances) == 2
    assert entry.state is ConfigEntryState.LOADED
    # And the identity rode along into the new set-up.
    assert entry.runtime_data.identity.serial == FOUND.serial
    assert hass.states.get(_entity_id(hass, "sensor", "serial")).state == FOUND.serial


async def test_a_walk_that_finds_nothing_new_writes_nothing(hass):
    with patch(
        f"custom_components.{DOMAIN}.async_read_identity_via_panel",
        AsyncMock(return_value=Identity()),
    ):
        entry = await _set_up(hass, **{CONF_IDENTITY: {"heatpump_model": "EcoAir 720M"}})
        await _let_the_background_run(hass)
    assert len(FakeModbus.instances) == 1
    assert entry.options[CONF_IDENTITY] == {"heatpump_model": "EcoAir 720M"}
    assert _issue(hass, entry, integration.ISSUE_IDENTITY) is not None


# ---------------------------------------------------------- the release check


async def test_a_reload_asks_github_nothing_and_a_quiet_github_leaves_the_notice(hass):
    integration._RELEASE_CHECKED.clear()
    github = AsyncMock(return_value="99.0.0")
    with patch(f"custom_components.{DOMAIN}.async_latest_release", github):
        entry = await _set_up(hass)
        await _let_the_background_run(hass)
        assert github.await_count == 1
        notice = _issue(hass, entry, integration.ISSUE_UPDATE_AVAILABLE)
        assert notice is not None
        assert notice.translation_placeholders["latest"] == "99.0.0"

        # A reload within the day is not a new question.
        assert await hass.config_entries.async_reload(entry.entry_id)
        await _let_the_background_run(hass)
        assert entry.state is ConfigEntryState.LOADED
        assert github.await_count == 1
        assert _issue(hass, entry, integration.ISSUE_UPDATE_AVAILABLE) is not None

        # A day on, GitHub is asked again but does not answer: the notice
        # stands, and the question is owed again rather than remembered.
        github.return_value = None
        integration._RELEASE_CHECKED[entry.entry_id] -= integration.UPDATE_CHECK_INTERVAL.total_seconds()
        remembered = integration._RELEASE_CHECKED[entry.entry_id]
        await _advance(hass, integration.UPDATE_CHECK_INTERVAL.total_seconds() + 1)
        assert github.await_count == 2
        assert _issue(hass, entry, integration.ISSUE_UPDATE_AVAILABLE) is not None
        assert integration._RELEASE_CHECKED[entry.entry_id] == remembered

        # And when it answers that nothing newer is out, the notice goes.
        github.return_value = "0.1.0"
        await _advance(hass, integration.UPDATE_CHECK_INTERVAL.total_seconds() + 1)
        assert github.await_count == 3
        assert _issue(hass, entry, integration.ISSUE_UPDATE_AVAILABLE) is None
        assert integration._RELEASE_CHECKED[entry.entry_id] > remembered
