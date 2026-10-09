"""Display rows move to the key of their place, entity ids untouched (L2), under a real core.

An installation upgraded from 0.18.0 carries its display rows under keys made
of their names. Here such an entry runs and harvests, its entities and its two
stores holding the name keys, and then the menu is read again as it is after
an update: the reading gives every row the key of its place, the menu is
written with the key each row had, and the set-up that follows moves every
entity's unique_id over before the platforms make them, renames the rows in
the seen and display stores, and leaves every entity id where it was. The same
from the options' "read the menu again". A second start changes nothing, a
row whose name is read better renames the entity and keeps it, and an entry
already standing at a new key is left beside the old one with a warning,
neither of them removed.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_row_keys.py
"""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    FakeModbus,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_rows_alarms import HEATPUMP, PAGE, SCREEN, ServingPanel  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

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
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402
from custom_components.ctc_ecozenith.keys import row_key  # noqa: E402

PREFIX = f"{DOMAIN}_{HOST}_"
RESCAN = f"custom_components.{DOMAIN}.config_flow.async_rescan_pages"
CLIENT = f"custom_components.{DOMAIN}.CtcWebClient"

#: The heat pump page as the sweep of this release reads it: the same rows at
#: the same places, each keyed by where it stands.
POSITIONED = SlowPage(
    page=HEATPUMP.page,
    title=HEATPUMP.title,
    screens=list(HEATPUMP.screens),
    values=[
        replace(value, key=row_key(PAGE, SCREEN, value.var_indices[0])) for value in HEATPUMP.values
    ],
)
#: Name key to place key, as the migration has to carry them.
EXPECTED = {old.key: new.key for old, new in zip(HEATPUMP.values, POSITIONED.values)}


def _rows(hass, entry) -> dict[str, str]:
    """The display rows in the registry: entity id to the key in its unique_id."""
    return {
        item.entity_id: item.unique_id[len(PREFIX):]
        for item in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if item.domain == "sensor" and item.unique_id.startswith(f"{PREFIX}p{PAGE}_")
    }


def _seed_the_seen_record(hass_storage, entry_id: str) -> None:
    """The seen record as 0.18.0 left it on disk, name keys and all.

    Written here rather than by a harvest: Home Assistant's store puts off a
    write that was asked for twice by the event loop's own clock, which the
    test's timers do not move, so the record a run builds never reaches the
    disk in a test. What matters is the file an upgrade finds.
    """
    key = f"{DOMAIN}_{entry_id}_seen"
    hass_storage[key] = {
        "version": 1,
        "minor_version": 2,
        "key": key,
        "data": {"keys": sorted([*EXPECTED, "outdoor_temp"]), "numeric": sorted([*EXPECTED, "outdoor_temp"])},
    }


async def _running_on_name_keys(hass, hass_storage):
    """An entry with the 0.18.0 menu that has harvested, with both stores on disk."""
    ServingPanel.vars = [72, 215, 35, 0]
    ServingPanel.header = []
    stored = pages_to_storage([HEATPUMP])
    real_add = MockConfigEntry.add_to_hass

    def add_and_seed(self, hass):
        _seed_the_seen_record(hass_storage, self.entry_id)
        return real_add(self, hass)

    with patch(CLIENT, ServingPanel), patch.object(MockConfigEntry, "add_to_hass", add_and_seed):
        entry = await _set_up(hass, **{CONF_SLOW_PAGES: stored, CONF_MENU: stored})
    with patch(CLIENT, ServingPanel):
        await _advance(hass, MIN_FIRST_DELAY + 1)
        await _advance(hass, 30)  # the harvest's delayed write
    assert entry.state is ConfigEntryState.LOADED
    names = _rows(hass, entry)
    assert sorted(names.values()) == sorted(EXPECTED), "fyra rader under namnnycklar"
    seen = hass_storage[f"{DOMAIN}_{entry.entry_id}_seen"]["data"]
    assert set(EXPECTED) <= set(seen["numeric"])
    display = hass_storage[f"{DOMAIN}_{entry.entry_id}_display"]["data"]
    assert set(display["values"]) == set(EXPECTED)
    return entry, names


def _assert_moved(hass, hass_storage, entry, names: dict[str, str]) -> None:
    after = _rows(hass, entry)
    assert set(after) == set(names), "samma entitets-id, inga nya, inga borttagna"
    assert after == {entity_id: EXPECTED[key] for entity_id, key in names.items()}
    registry = er.async_get(hass)
    for entity_id, old_key in names.items():
        assert registry.async_get(entity_id).previous_unique_id == f"{PREFIX}{old_key}"
    # The stores follow, so nothing about a row is forgotten.
    seen = hass_storage[f"{DOMAIN}_{entry.entry_id}_seen"]["data"]
    assert set(EXPECTED.values()) <= set(seen["numeric"])
    assert not set(EXPECTED) & (set(seen["numeric"]) | set(seen["keys"]))
    display = hass_storage[f"{DOMAIN}_{entry.entry_id}_display"]["data"]
    assert set(display["values"]) == set(EXPECTED.values())
    assert set(display["read_at"]) == set(EXPECTED.values())
    assert entry.runtime_data.seen.numeric >= set(EXPECTED.values())
    # And the entities come up with the stored harvest, values and all.
    by_key = {key: entity_id for entity_id, key in after.items()}
    assert hass.states.get(by_key[row_key(PAGE, SCREEN, 0)]).state == "7.2"
    assert hass.states.get(by_key[row_key(PAGE, SCREEN, 3)]).state == "0.0"


# ------------------------------------------------- the reading after an update


async def test_the_reading_after_an_update_moves_every_row_and_keeps_the_entity_ids(
    hass, hass_storage, stubs
):
    entry, names = await _running_on_name_keys(hass, hass_storage)

    # An update: the menu is owed a reading, and the display gives the whole of it.
    stubs.discover.return_value = MenuReading(pages=[POSITIONED], complete=True, root=None)
    with patch(CLIENT, ServingPanel):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_MENU_VERSION: "0.17.0"}
        )
        await hass.async_block_till_done()
        await _let_the_background_run(hass)
        await hass.async_block_till_done()
    assert stubs.discover.await_count == 1
    assert entry.state is ConfigEntryState.LOADED

    menu = pages_from_storage(entry.options[CONF_MENU])
    assert {v.key: v.previous_key for v in menu[0].values} == {new: old for old, new in EXPECTED.items()}
    _assert_moved(hass, hass_storage, entry, names)

    # A second start finds nothing left to move, and removes nothing.
    with patch(CLIENT, ServingPanel):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    _assert_moved(hass, hass_storage, entry, names)


async def test_read_again_from_the_options_moves_the_rows_the_same_way(hass, hass_storage, stubs):
    entry, names = await _running_on_name_keys(hass, hass_storage)
    whole = MenuReading(pages=[POSITIONED], complete=True)
    with patch(RESCAN, AsyncMock(return_value=whole)), patch(CLIENT, ServingPanel):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(flow["flow_id"], {"rescan": True})
        assert result["step_id"] == "rescan"
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_SLOW_PAGES: [str(PAGE)]}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    _assert_moved(hass, hass_storage, entry, names)


async def test_a_name_read_better_renames_the_entity_and_keeps_it(hass, hass_storage, stubs):
    entry, names = await _running_on_name_keys(hass, hass_storage)
    outdoor = next(e for e, key in names.items() if key == f"p{PAGE}_utetemperatur")
    better = replace(
        POSITIONED,
        values=[replace(POSITIONED.values[0], label="Utetemperatur givare")] + POSITIONED.values[1:],
    )
    stubs.discover.return_value = MenuReading(pages=[better], complete=True)
    with patch(CLIENT, ServingPanel):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_MENU_VERSION: "0.17.0"}
        )
        await hass.async_block_till_done()
        await _let_the_background_run(hass)
        await hass.async_block_till_done()
    item = er.async_get(hass).async_get(outdoor)
    assert item.unique_id == f"{PREFIX}{row_key(PAGE, SCREEN, 0)}"
    assert item.original_name == "Värmepump: Utetemperatur givare"
    assert hass.states.get(outdoor).state == "7.2"


async def test_an_entry_already_at_the_new_key_is_left_beside_the_old_one(hass, stubs, caplog):
    """Not a state any release leaves behind, so the owner says which one goes."""
    marked = replace(
        POSITIONED,
        values=[replace(v, previous_key=old) for v, old in zip(POSITIONED.values, EXPECTED)],
    )
    stored = pages_to_storage([marked])
    ServingPanel.vars = [72, 215, 35, 0]
    ServingPanel.header = []
    registry = er.async_get(hass)
    seeded: dict[str, str] = {}
    real_add = MockConfigEntry.add_to_hass

    def add_and_seed(self, hass):
        # Both entries in the registry before the set-up, as a start before left them.
        result = real_add(self, hass)
        seeded["old"] = registry.async_get_or_create(
            "sensor", DOMAIN, f"{PREFIX}p{PAGE}_utetemperatur", config_entry=self,
            suggested_object_id="gamla_utetemperaturen",
        ).entity_id
        seeded["new"] = registry.async_get_or_create(
            "sensor", DOMAIN, f"{PREFIX}{row_key(PAGE, SCREEN, 0)}", config_entry=self,
            suggested_object_id="nya_utetemperaturen",
        ).entity_id
        return result

    caplog.set_level(logging.WARNING)
    with patch(CLIENT, ServingPanel), patch.object(MockConfigEntry, "add_to_hass", add_and_seed):
        entry = await _set_up(hass, **{CONF_SLOW_PAGES: stored, CONF_MENU: stored})
    assert entry.state is ConfigEntryState.LOADED
    old = registry.async_get(seeded["old"])
    assert old is not None, "den gamla tas inte bort"
    assert old.unique_id == f"{PREFIX}p{PAGE}_utetemperatur"
    assert registry.async_get(seeded["new"]).unique_id == f"{PREFIX}{row_key(PAGE, SCREEN, 0)}"
    assert "could not be carried over" in caplog.text
    assert seeded["old"] in caplog.text and seeded["new"] in caplog.text
    # The other three rows moved as ever: their old keys had no entries here,
    # so nothing else was said.
    assert caplog.text.count("could not be carried over") == 1
