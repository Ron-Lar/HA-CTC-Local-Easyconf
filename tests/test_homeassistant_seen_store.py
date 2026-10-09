"""The seen store across a return to the release before, under a real Home Assistant core.

The record of which rows have been a number (L3) is a new shape of the seen
store. It was first written as major version 2, and Home Assistant refuses to
load a file whose major version is above the one the code opens it with: the
release before opens the file as a plain Store at version 1, with nothing to
catch the refusal, so a return to it after an update would have left the entry
in SETUP_ERROR with every entity gone until somebody deleted the file by hand.
The shape is now minor 2 of major 1. Here the file goes both ways: one the
release before wrote is brought up to minor 2, and one this release wrote is
read by a plain Store at version 1 exactly as that release reads it, keeps its
keys, is written back as 1.1, and comes back up to 1.2 at the next start.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_seen_store.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    _needs_auto_asyncio_mode,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.helpers.storage import Store  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)

ENTRY = "abc123"
KEY = f"{DOMAIN}_{ENTRY}_seen"

#: What the release before kept: the keys that had been something other than zero.
BEFORE = {"keys": ["hp1_rps", "p22_utetemperatur", "system_status"]}

#: What this release keeps: the same keys, and the keys that have been a number.
NOW = {
    "keys": ["hp1_rps", "p22_utetemperatur", "system_status"],
    "numeric": ["hp1_fan", "hp1_rps", "p22_utetemperatur"],
}


def _file(version: int, minor: int, data: dict) -> dict:
    return {"version": version, "minor_version": minor, "key": KEY, "data": data}


def _on_disk(hass_storage) -> tuple[int, int, dict]:
    stored = hass_storage[KEY]
    return stored["version"], stored["minor_version"], stored["data"]


# --------------------------------------------------------- the way forward


async def test_a_file_from_the_release_before_is_brought_up_to_minor_two(hass, hass_storage, stubs):
    hass_storage[KEY] = _file(1, 1, BEFORE)

    data = await integration.seen_store(hass, ENTRY).async_load()

    assert data == {
        "keys": ["hp1_rps", "p22_utetemperatur", "system_status"],
        "numeric": ["p22_utetemperatur"],
    }, "displayraden blir numerisk, Modbus-nycklarna antas inte"
    assert _on_disk(hass_storage) == (1, 2, data), "skriven som 1.2, inte som 2"


# ------------------------------------------------------------ the way back


async def test_a_file_this_release_wrote_is_read_by_the_release_before_and_comes_back(
    hass, hass_storage, stubs
):
    """Store(hass, 1, key) is exactly how the release before opens the file."""
    hass_storage[KEY] = _file(1, 2, NOW)

    data = await Store(hass, 1, KEY).async_load()

    assert data["keys"] == NOW["keys"], "nycklarna läses som förut"
    assert _on_disk(hass_storage)[:2] == (1, 1), "och skrivs tillbaka som 1.1"
    assert _on_disk(hass_storage)[2]["numeric"] == NOW["numeric"], "med det okända bevarat"

    # That release learns a display row and writes the keys alone, as it does.
    hass_storage[KEY] = _file(1, 1, {"keys": NOW["keys"] + ["p22_fläkt"]})

    # Back on this release: up to 1.2 again, with the rows learnt meanwhile.
    data = await integration.seen_store(hass, ENTRY).async_load()
    assert _on_disk(hass_storage)[:2] == (1, 2)
    assert set(data["numeric"]) == {"p22_utetemperatur", "p22_fläkt"}


async def test_a_file_at_the_current_shape_is_read_as_it_is(hass, hass_storage, stubs):
    hass_storage[KEY] = _file(1, 2, NOW)
    assert await integration.seen_store(hass, ENTRY).async_load() == NOW
    assert _on_disk(hass_storage) == (1, 2, NOW), "ingen migrering, ingen omskrivning"


# ----------------------------------------------------------- the whole entry


async def test_the_entry_comes_up_on_the_file_the_release_before_left(hass, hass_storage, stubs):
    """The record is read through set-up, and the entry loads: no SETUP_ERROR either way."""
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={CONF_HOST: HOST, CONF_MODBUS_PORT: 502, CONF_WEB_PORT: 80, CONF_SLAVE: 1, "model": MODEL},
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY},
    )
    entry.add_to_hass(hass)
    key = f"{DOMAIN}_{entry.entry_id}_seen"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": BEFORE}

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    seen = entry.runtime_data.seen
    assert not seen.fresh, "en lagrad post: ingen sådd ur statistiken"
    assert {"hp1_rps", "p22_utetemperatur", "system_status"} <= seen.keys
    assert "p22_utetemperatur" in seen.numeric
    stored = hass_storage[key]
    assert (stored["version"], stored["minor_version"]) == (1, 2)
