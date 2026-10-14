"""The share of auxiliary heat under a real Home Assistant core (roadmap R28).

A history page with both energy counters is harvested, and Modbus answers the
immersion heater's counter (62191) and the compressor's running hours (62214).
PT's i550 Pro, 912 kWh of immersion heater against a compressor counter at
zero, reads a hundred per cent; VSH's i255, 1 of 9358 kWh, reads 0.0; a
compressor counter at zero beside a compressor that has run gives no share and
says why. The sample the six hour timer writes carries the immersion heater's
counter of the same moment, in a store at minor 2, a store an older release
wrote at minor 1 is read and brought up, and the release before reads the new
file through Home Assistant's own Store without a refusal. The sample taken
when the entry is set up again keeps the counter written down with the
harvest. What can be read without a core is in test_aux_share.py.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
panel of test_homeassistant_harvest.py, and runs the same way:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_aux_share.py
"""

from __future__ import annotations

from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers.storage import Store
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    READINGS,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs_fixture,
)
from test_homeassistant_harvest import INTERVAL, LivePanel, _fire

from custom_components.ctc_ecozenith import COP_SAMPLE_INTERVAL
from custom_components.ctc_ecozenith.catalogue import pages_to_storage
from custom_components.ctc_ecozenith.const import (
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
    SlowPage,
    SlowValue,
)
from custom_components.ctc_ecozenith.cop import (
    SHARE_NOT_SAMPLED,
    SHARE_SPANS,
    STORAGE_MINOR_VERSION,
    CopTracker,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY, SAVE_DELAY_SECONDS

HISTORY = 25
HEAT = "p25_avgiven_varme_totalt"
CONSUMED = "p25_tillford_energi_totalt"


def _row(key: str, label: str, index: int) -> SlowValue:
    return SlowValue(
        key=key, label=label, page=HISTORY, screen=HISTORY * 10, fmt="%d",
        var_indices=[index], unit="kWh",
    )


PAGES = [
    SlowPage(
        page=HISTORY,
        title="Historik",
        screens=[HISTORY * 10],
        values=[
            _row(HEAT, "Avgiven värme totalt", 0),
            _row(CONSUMED, "Tillförd energi totalt", 1),
        ],
        route=[(720, 255)],
    ),
]


class CounterPanel(LivePanel):
    """The answering panel, whose history page gives the two counters set per test."""

    counters: list[int] = [0, 0]

    async def async_vars(self, screen) -> list:
        if screen == HISTORY * 10:
            return list(CounterPanel.counters)
        return await super().async_vars(screen)


@pytest.fixture(autouse=True)
def counter_panel(stubs, monkeypatch):
    LivePanel.instances.clear()
    import custom_components.ctc_ecozenith as integration

    monkeypatch.setattr(integration, "CtcWebClient", CounterPanel)
    yield


def _pump(monkeypatch, *, heat: int, consumed: int, immersion: int, compressor_hours: int):
    """The display's two counters and Modbus 62191 and 62214, low word first."""
    monkeypatch.setattr(CounterPanel, "counters", [heat, consumed])
    monkeypatch.setitem(READINGS, 62191, immersion)
    monkeypatch.setitem(READINGS, 62214, compressor_hours)
    monkeypatch.setitem(READINGS, 62215, 0)


async def _entry(hass) -> MockConfigEntry:
    stored = pages_to_storage(PAGES)
    version = str((await async_get_integration(hass, DOMAIN)).version)
    return MockConfigEntry(
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


async def _harvested(hass, entry: MockConfigEntry) -> None:
    """Set up with nothing written down, and let the first harvest read the counters."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert entry.runtime_data.web.last_read(HEAT) is not None


def _state(hass, span: str):
    return hass.states.get(_entity_id(hass, "sensor", f"aux_share_{span}"))


def _cop_key(entry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_cop"


async def test_pt_reads_a_hundred_per_cent_and_the_sample_carries_the_counter(
    hass, hass_storage, monkeypatch
):
    _pump(monkeypatch, heat=0, consumed=0, immersion=912, compressor_hours=0)
    entry = await _entry(hass)
    await _harvested(hass, entry)

    lifetime = _state(hass, "lifetime")
    assert float(lifetime.state) == 100.0
    assert lifetime.attributes["elpatron kWh"] == lifetime.attributes["immersion_kwh"] == 912.0
    assert lifetime.attributes["kompressor kWh"] == lifetime.attributes["compressor_kwh"] == 0.0
    assert lifetime.attributes["unit_of_measurement"] == "%"
    assert lifetime.attributes["state_class"] == "measurement"
    assert "reason" not in lifetime.attributes
    for span in ("day", "week", "month", "year"):
        state = _state(hass, span)
        assert state.state == "unknown", span
        assert state.attributes["reason"] == state.attributes["skäl"], span
    assert _state(hass, "day").attributes["reason"] == (
        "väntar på ett prov som är 20 till 30 timmar gammalt"
    )

    # The six hour timer writes a sample: the counters and the immersion
    # heater's, all of the moment the panel was read.
    read = entry.runtime_data.web.last_read(HEAT)
    await _fire(hass, COP_SAMPLE_INTERVAL.total_seconds() + 10)
    stored = hass_storage[_cop_key(entry)]
    assert (stored["version"], stored["minor_version"]) == (1, STORAGE_MINOR_VERSION) == (1, 2)
    rows = stored["data"]["recent"]
    assert rows, "a sample was written"
    assert all(len(row) == 4 and row[3] == 912.0 for row in rows)
    assert rows[0][0] >= read.isoformat()
    day = next(iter(stored["data"]["samples"].values()))
    assert day == [0.0, 0.0, 912.0]


async def test_vsh_reads_nought_with_the_kilowatt_hours_beside_it(hass, monkeypatch):
    _pump(monkeypatch, heat=22510, consumed=9357, immersion=1, compressor_hours=8000)
    entry = await _entry(hass)
    await _harvested(hass, entry)
    lifetime = _state(hass, "lifetime")
    assert float(lifetime.state) == 0.0
    assert lifetime.attributes["immersion_kwh"] == 1.0
    assert lifetime.attributes["compressor_kwh"] == 9357.0
    assert lifetime.attributes["basis"] == "hela livslängden"


async def test_a_compressor_that_ran_without_counting_gives_no_share(hass, monkeypatch):
    _pump(monkeypatch, heat=0, consumed=0, immersion=912, compressor_hours=100)
    entry = await _entry(hass)
    await _harvested(hass, entry)
    for span in SHARE_SPANS:
        state = _state(hass, span)
        assert state.state == "unknown", span
        assert state.attributes["reason"] == (
            "kompressorns räknare står på noll fast kompressorn har gått 100 timmar, "
            "styrenheten fyller den inte"
        ), span


def _cop_file(entry, minor: int, row: list) -> dict:
    return {
        "version": 1,
        "minor_version": minor,
        "key": _cop_key(entry),
        "data": {"samples": {}, "recent": [row], "anchor": None, "first_year": None},
    }


async def test_a_day_against_a_sample_with_the_counter(hass, hass_storage, monkeypatch):
    _pump(monkeypatch, heat=0, consumed=0, immersion=912, compressor_hours=0)
    entry = await _entry(hass)
    base = (dt_util.utcnow() - timedelta(hours=24)).isoformat()
    hass_storage[_cop_key(entry)] = _cop_file(entry, 2, [base, 0.0, 0.0, 900.0])
    await _harvested(hass, entry)
    day = _state(hass, "day")
    assert float(day.state) == 100.0
    assert (day.attributes["immersion_kwh"], day.attributes["compressor_kwh"]) == (12.0, 0.0)


async def test_a_store_an_older_release_wrote_is_read_and_brought_up(
    hass, hass_storage, monkeypatch
):
    _pump(monkeypatch, heat=0, consumed=0, immersion=912, compressor_hours=0)
    entry = await _entry(hass)
    base = (dt_util.utcnow() - timedelta(hours=24)).isoformat()
    hass_storage[_cop_key(entry)] = _cop_file(entry, 1, [base, 0.0, 0.0])
    await _harvested(hass, entry)
    assert hass_storage[_cop_key(entry)]["minor_version"] == 2
    assert hass_storage[_cop_key(entry)]["data"]["recent"][0] == [base, 0.0, 0.0]
    day = _state(hass, "day")
    assert day.state == "unknown"
    assert day.attributes["reason"] == SHARE_NOT_SAMPLED


async def test_the_release_before_opens_the_new_file_without_a_refusal(hass, hass_storage):
    key = "ctc_ecozenith_test_cop"
    tracker = CopTracker(Store(hass, 1, key, minor_version=STORAGE_MINOR_VERSION))
    read = dt_util.utcnow() - timedelta(hours=24)
    await tracker.async_record(22499.0, 9116.0, now=read, immersion=1.0)
    assert hass_storage[key]["minor_version"] == 2

    # 0.21.1 opens the file as a plain Store at version 1, minor 1: Home
    # Assistant migrates the minor by handing the data over as it is.
    older = await Store(hass, 1, key).async_load()
    (row,) = older["recent"]
    assert (str(row[0]), float(row[1]), float(row[2])) == (read.isoformat(), 22499.0, 9116.0)
    (sample,) = older["samples"].values()
    assert [float(sample[0]), float(sample[1])] == [22499.0, 9116.0]
    assert hass_storage[key]["minor_version"] == 1, "the release before writes it back as 1.1"

    # And this release reads it again with the immersion heater still there.
    again = CopTracker(Store(hass, 1, key, minor_version=STORAGE_MINOR_VERSION))
    await again.async_load()
    assert again._recent[0][3] == 1.0
    assert hass_storage[key]["minor_version"] == 2


async def test_the_sample_at_a_reload_keeps_the_immersion_heater_of_the_harvest(
    hass, hass_storage, monkeypatch
):
    # F2.2: the six hour timer writes a sample with the counter, a harvest
    # after it is written down with 62191 beside the heat counter, and the
    # entry is set up again, as saved options or a restart do.
    _pump(monkeypatch, heat=22020, consumed=9020, immersion=1, compressor_hours=8000)
    entry = await _entry(hass)
    await _harvested(hass, entry)
    await _fire(hass, COP_SAMPLE_INTERVAL.total_seconds() + 10)
    timer = hass_storage[_cop_key(entry)]["data"]["recent"][-1]
    assert timer[3] == 1.0
    monkeypatch.setattr(CounterPanel, "counters", [22021, 9021])
    await _fire(hass, INTERVAL + 1)
    await _fire(hass, SAVE_DELAY_SECONDS + 1)
    display = hass_storage[f"{DOMAIN}_{entry.entry_id}_display"]
    assert (display["version"], display["minor_version"]) == (1, 1)
    assert display["data"]["immersion"] == 1.0
    read = display["data"]["read_at"][HEAT]
    assert read > timer[0], "the harvest is newer than the timer's sample"

    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    stored = hass_storage[_cop_key(entry)]["data"]
    # The sample of the stored harvest's moment replaces the day's, and
    # keeps the immersion heater's counter rather than dropping it.
    assert stored["recent"][-1] == [read, 22021.0, 9021.0, 1.0]
    day = dt_util.parse_datetime(read).date().isoformat()
    assert stored["samples"][day] == [22021.0, 9021.0, 1.0]
