"""A restart costs the panel nothing: the last harvest is kept and the next is owed (R6).

Set-up used to harvest the display inside itself, so every restart of Home
Assistant walked the panel before the platforms existed, and nothing of the
display survived the restart, so the sensors stood unavailable until the walk
was done. Now the last harvest, values and moments alike, is written to a
store, the coordinator comes up with it, and the first harvest is scheduled
one interval after the last reading, at once only where nothing was stored.
The run under a real core is in test_homeassistant_harvest.py; these hold the
rules themselves and the coordinator's start.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

import ha_stub
from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

INTERVAL = 1800
NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def harvest():
    return load("harvest")


class FakeStore:
    """Stands in for Home Assistant's Store: what was loaded, what was scheduled."""

    def __init__(self, stored=None) -> None:
        self.stored = stored
        self.saves: list[tuple[dict, int]] = []

    async def async_load(self):
        return self.stored

    def async_delay_save(self, data_func, delay):
        self.saves.append((data_func(), delay))


def _stored(harvest, age: timedelta, **extra):
    read = NOW - age
    return harvest.StoredHarvest(
        values={"p22_avgiven_varme": 11.1, "p30_total_drifttid": 8044.0},
        read_at={"p22_avgiven_varme": read, "p30_total_drifttid": read},
        harvested_at=read,
        **extra,
    )


# ------------------------------------------------------------- the store


def test_a_harvest_survives_the_round_trip_through_json(harvest):
    before = _stored(harvest, timedelta(minutes=10), consumption=9166.0)
    payload = before.as_dict()
    # Plain JSON types only: floats, strings, None.
    assert payload["values"] == {"p22_avgiven_varme": 11.1, "p30_total_drifttid": 8044.0}
    assert all(isinstance(moment, str) for moment in payload["read_at"].values())
    assert isinstance(payload["harvested_at"], str)
    after = harvest.StoredHarvest.from_dict(payload)
    assert after.values == before.values
    assert after.read_at == before.read_at
    assert after.harvested_at == before.harvested_at
    assert after.consumption == 9166.0
    assert after.latest == before.harvested_at


def test_what_cannot_be_judged_is_left_out_of_the_seed(harvest):
    payload = {
        "values": {"a": 1.0, "b": "text", "c": True, "d": 4.0},
        "read_at": {"a": "2026-10-09T11:50:00+00:00", "d": "not a moment"},
        "harvested_at": None,
        "consumption": "9166",
    }
    stored = harvest.StoredHarvest.from_dict(payload)
    # b is no number, c is a bool, d has no moment to age by.
    assert stored.values == {"a": 1.0}
    assert stored.read_at == {"a": datetime(2026, 10, 9, 11, 50, tzinfo=timezone.utc)}
    assert stored.consumption is None, "en text är inget tal"
    assert harvest.StoredHarvest.from_dict(None) is None
    assert harvest.StoredHarvest.from_dict("junk") is None
    assert harvest.StoredHarvest.from_dict({"values": {}, "read_at": {}}) is None, (
        "inget läst: inget att så"
    )


def test_a_moment_without_a_zone_is_taken_as_utc(harvest):
    assert harvest.parse_moment("2026-10-09T11:50:00") == datetime(
        2026, 10, 9, 11, 50, tzinfo=timezone.utc
    )
    assert harvest.parse_moment("") is None and harvest.parse_moment(5) is None


def test_the_memory_writes_once_per_harvest(harvest):
    store = FakeStore()
    memory = harvest.HarvestMemory(store)
    assert run(memory.async_load()) is None
    harvested = NOW
    values = {"p22_avgiven_varme": 11.1, "gone": None}
    read_at = {"p22_avgiven_varme": harvested, "stale_key": harvested - timedelta(days=1)}
    assert memory.remember(values, read_at, harvested, 9166.0)
    # Every refresh calls again, a skipped or failed one included: no new save.
    assert not memory.remember(values, read_at, harvested, 9166.0)
    assert not memory.remember(values, read_at, None)
    assert memory.remember(values, read_at, harvested + timedelta(minutes=30))
    assert len(store.saves) == 2
    payload, delay = store.saves[0]
    assert delay == harvest.SAVE_DELAY_SECONDS
    # Only numbers, and only moments of keys that are in the data.
    assert payload["values"] == {"p22_avgiven_varme": 11.1}
    assert set(payload["read_at"]) == {"p22_avgiven_varme"}
    assert payload["consumption"] == 9166.0


def test_the_memory_knows_what_it_loaded(harvest):
    stored = _stored(harvest, timedelta(minutes=10))
    store = FakeStore(stored.as_dict())
    memory = harvest.HarvestMemory(store)
    loaded = run(memory.async_load())
    assert loaded.values == stored.values
    # The harvest it loaded is already remembered: no save for it.
    assert not memory.remember(loaded.values, loaded.read_at, loaded.harvested_at)
    assert store.saves == []


def test_a_store_that_cannot_be_read_is_an_empty_one(harvest):
    class Broken(FakeStore):
        async def async_load(self):
            raise OSError("disk")

    assert run(harvest.HarvestMemory(Broken()).async_load()) is None


# -------------------------------------------------------- the first harvest


def test_without_a_store_the_first_harvest_is_at_once(harvest):
    assert harvest.first_harvest_delay(None, INTERVAL, NOW) == harvest.MIN_FIRST_DELAY


def test_a_restart_within_the_interval_waits_out_the_rest_of_it(harvest):
    stored = _stored(harvest, timedelta(minutes=10))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == 20 * 60


def test_a_longer_absence_harvests_as_soon_as_the_platforms_are_up(harvest):
    stored = _stored(harvest, timedelta(hours=3))
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == harvest.MIN_FIRST_DELAY


def test_the_latest_moment_counts_whichever_it_is(harvest):
    older = NOW - timedelta(hours=2)
    stored = harvest.StoredHarvest(
        values={"a": 1.0}, read_at={"a": older}, harvested_at=NOW - timedelta(minutes=5)
    )
    assert stored.latest == NOW - timedelta(minutes=5)
    assert harvest.first_harvest_delay(stored, INTERVAL, NOW) == 25 * 60


# ----------------------------------------------------- the coordinator


@pytest.fixture(scope="module")
def coordinator():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


class IdlePanel:
    """A display nobody talks to: the start must not."""

    def __init__(self) -> None:
        self.panel = asyncio.Lock()
        self.asked = 0

    def __getattr__(self, name):
        if name.startswith("async_"):
            async def refuse(*args, **kwargs):
                self.asked += 1
                raise AssertionError(f"{name} asked the display during the start")

            return refuse
        raise AttributeError(name)


def _page(const, number):
    return const.SlowPage(
        page=number,
        title=f"Sida {number}",
        screens=[number * 10],
        values=[const.SlowValue(key=f"p{number}_v", label="V", page=number, screen=number * 10, fmt="%d", var_indices=[0])],
    )


def test_the_coordinator_comes_up_with_the_stored_harvest(coordinator, harvest, const):
    read = datetime.now(timezone.utc) - timedelta(minutes=10)
    stored = harvest.StoredHarvest(
        values={"p21_v": 21.0}, read_at={"p21_v": read}, harvested_at=read
    )
    panel = IdlePanel()
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=panel, pages=[_page(const, 21)], interval=INTERVAL, stored=stored
    )
    assert web.data == {"p21_v": 21.0}, "sensorerna har något att visa från start"
    assert web.last_read("p21_v") == read
    assert web.last_harvest == read
    assert web.is_fresh("p21_v")
    # The first harvest is owed one interval after the last reading.
    wait = web.update_interval.total_seconds()
    assert 19 * 60 < wait <= 20 * 60
    assert web.next_attempt is not None
    assert panel.asked == 0, "starten frågar inte displayen"


def test_an_old_store_seeds_stale_values_and_an_early_harvest(coordinator, harvest, const):
    read = datetime.now(timezone.utc) - timedelta(days=2)
    stored = harvest.StoredHarvest(values={"p21_v": 21.0}, read_at={"p21_v": read}, harvested_at=read)
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=IdlePanel(), pages=[_page(const, 21)], interval=INTERVAL, stored=stored
    )
    assert web.data == {"p21_v": 21.0}
    assert not web.is_fresh("p21_v"), "två dygn gammalt visas inte som nu"
    assert web.update_interval == timedelta(seconds=harvest.MIN_FIRST_DELAY)


def test_without_a_store_the_coordinator_starts_empty_and_soon(coordinator, harvest, const):
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=IdlePanel(), pages=[_page(const, 21)], interval=INTERVAL
    )
    assert web.data is None
    assert web.last_harvest is None
    assert web.update_interval == timedelta(seconds=harvest.MIN_FIRST_DELAY)


def test_the_consumption_pair_comes_back_as_a_pair(cop):
    snapshot = cop.ConsumptionSnapshot()
    read = NOW - timedelta(minutes=10)
    snapshot.seed(read, 9166.0)
    assert snapshot.value == 9166.0
    # The same moment again, with a fresh Modbus reading: the pair stands.
    snapshot.update(read, {"compressor_kwh": 9170.0})
    assert snapshot.value == 9166.0
    # A new reading of the display takes the new Modbus value.
    snapshot.update(NOW, {"compressor_kwh": 9170.0})
    assert snapshot.value == 9170.0
    # Nothing stored: nothing seeded.
    empty = cop.ConsumptionSnapshot()
    empty.seed(None, 9166.0)
    assert empty.value is None


# ------------------------------------------------------------- in code


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_the_set_up_seeds_the_coordinator_and_remembers_after_each_harvest():
    setup = _source("__init__.py").split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "HarvestMemory(" in setup
    assert "stored=stored" in setup, "skördaren sås ur storen"
    assert "memory.remember(" in setup
    assert "web.async_add_listener(_remember)" in setup
    # The energy counters are recorded with the moment they were read.
    assert "_record_cop(read_at=" in setup
    assert "snapshot.seed(" in setup


def test_removing_the_entry_takes_all_three_stores_with_it():
    remove = _source("__init__.py").split("async def async_remove_entry")[1]
    for suffix in ("_seen", "_display", "_cop"):
        assert f'_{{entry.entry_id}}{suffix}"' in remove, f"storen {suffix} tas inte bort"
    assert remove.count(".async_remove()") == 3


def test_the_identity_is_read_in_the_background_and_swept_once_per_run():
    source = _source("__init__.py")
    catch_up = source.split("async def _async_catch_up")[1].split("\ndef ")[0]
    assert "await async_read_identity(" in catch_up
    assert "_SWEPT" in catch_up
    # And the options are written with the panel free, so the reload that
    # follows cuts no harvest short.
    writing = catch_up.split("if changed:")[1]
    assert writing.index("async with client.panel:") < writing.index("async_update_entry(")
