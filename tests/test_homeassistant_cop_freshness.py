"""The coefficient of performance and the mean run when the history page stops
being reached, under a real Home Assistant core (F3.1, F4.1, F3.2).

Two pages are harvested: a heating page that always answers and the history
page with both energy counters and the starts per day. The display store comes
back from two days ago, as after a long restart, and the history page is out
of reach for the first harvests while the other page is read. The harvest is
then no failure (R5): the display reports success, the history rows age, and
what rests on them has to follow the rows and not the display. The day goes
unavailable with the rows, the other spans say which row is old and since
when, the mean run goes empty, and the six hour sample timer takes no sample
off the frozen pair. When the page is reached again everything comes back, and
the next sample bears the moment the counters were read.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
panel of test_homeassistant_harvest.py, and runs the same way, from a virtual
environment that has Home Assistant and pytest-homeassistant-custom-component
installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_cop_freshness.py
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    READINGS,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_harvest import INTERVAL, LivePanel, _fire, _prefill  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

from custom_components.ctc_ecozenith import COP_SAMPLE_INTERVAL  # noqa: E402
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
    SlowPage,
    SlowValue,
)
from custom_components.ctc_ecozenith.cop import LAST_HARVEST_ATTRIBUTE  # noqa: E402
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402

HEATING_PAGE = 21
HISTORY = 25
HEAT = "p25_avgiven_varme_totalt"
CONSUMED = "p25_tillford_energi_totalt"
STARTS = "p25_antal_starter_24"
#: What the history page answers once it is reached: the counters moved on,
#: eight starts over the day.
HISTORY_VARS = [22510, 9120, 8]


def _history_row(key: str, label: str, index: int, unit: str | None) -> SlowValue:
    return SlowValue(
        key=key, label=label, page=HISTORY, screen=HISTORY * 10, fmt="%d",
        var_indices=[index], unit=unit,
    )


PAGES = [
    SlowPage(
        page=HEATING_PAGE,
        title="Värmesystem",
        screens=[HEATING_PAGE * 10],
        values=[
            SlowValue(
                key="p21_utetemperatur", label="Utetemperatur", page=HEATING_PAGE,
                screen=HEATING_PAGE * 10, fmt="%.1f°C", var_indices=[0], unit="°C", scale=0.1,
            )
        ],
        route=[(80, 255)],
    ),
    SlowPage(
        page=HISTORY,
        title="Historik",
        screens=[HISTORY * 10],
        values=[
            _history_row(HEAT, "Avgiven värme totalt", 0, "kWh"),
            _history_row(CONSUMED, "Tillförd energi totalt", 1, "kWh"),
            _history_row(STARTS, "Antal starter /24", 2, None),
        ],
        route=[(720, 255)],
    ),
]


class HistoryPanel(LivePanel):
    """The answering panel, with the history page out of reach on demand."""

    blocked: set[int] = set()

    async def async_goto_page(self, target: int, route=None) -> bool:
        if target in HistoryPanel.blocked:
            return False
        return await super().async_goto_page(target, route)

    async def async_vars(self, screen) -> list:
        if screen == HISTORY * 10:
            return list(HISTORY_VARS)
        return await super().async_vars(screen)


@pytest.fixture(autouse=True)
def history_panel(stubs, monkeypatch):
    LivePanel.instances.clear()
    HistoryPanel.blocked = set()
    import custom_components.ctc_ecozenith as integration

    monkeypatch.setattr(integration, "CtcWebClient", HistoryPanel)
    # Modbus 62234: 240 minutes of compressor time over the day.
    monkeypatch.setitem(READINGS, 62234, 240)
    yield


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


async def _set_up(hass, entry: MockConfigEntry) -> HistoryPanel:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    (panel,) = LivePanel.instances
    return panel


def _state(hass, key: str):
    return hass.states.get(_entity_id(hass, "sensor", key))


def _stamps(hass_storage, entry) -> list[str]:
    return [row[0] for row in hass_storage[f"{DOMAIN}_{entry.entry_id}_cop"]["data"]["recent"]]


async def test_a_history_page_out_of_reach_leaves_no_figure_and_no_sample(hass, hass_storage, stubs):
    read = (dt_util.utcnow() - timedelta(days=2)).replace(microsecond=0)
    entry = await _entry(hass)
    _prefill(
        hass_storage, entry, read,
        {HEAT: 22499.0, CONSUMED: 9116.0, STARTS: 8.0, "p21_utetemperatur": 7.2},
    )
    HistoryPanel.blocked = {HISTORY}
    panel = await _set_up(hass, entry)
    web = entry.runtime_data.web

    # The stored pair is a sample at its own moment, two days ago, kept once.
    assert _stamps(hass_storage, entry) == [read.isoformat()]

    notice = f"displayens rad Avgiven värme totalt har inte lästs sedan {read.isoformat()}"

    def _stale() -> None:
        # The rows' own sensors are unavailable, and so is the day; the other
        # spans stand and say which row is old; the mean run has nothing to
        # divide by and says when the starts were last read.
        assert _state(hass, STARTS).state == "unavailable"
        assert _state(hass, "cop_day").state == "unavailable"
        for span in ("cop_week", "cop_month", "cop_year", "cop_lifetime"):
            state = _state(hass, span)
            assert state.state == "unknown", span
            assert state.attributes["skäl"] == notice, span
        mean = _state(hass, "mean_run_24h")
        assert mean.state == "unknown"
        assert mean.attributes["kompressordrift senaste dygnet"] == 240
        assert mean.attributes["antal starter /24 h"] is None
        assert mean.attributes["antal starter senast läst"] == read.isoformat()

    _stale()

    # The first harvest reads the heating page and misses the history page:
    # no failure of the display, the history rows simply age on.
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert panel.walked == [HEATING_PAGE]
    assert web.last_update_success
    assert web.pages_read == [HEATING_PAGE] and web.pages_missed == [HISTORY]
    assert _state(hass, "p21_utetemperatur").state == "7.2"
    _stale()
    # The coordinator's own moment, by the name the notice reads it under.
    assert isinstance(web.last_harvest, datetime)
    assert getattr(web, LAST_HARVEST_ATTRIBUTE) is web.last_harvest

    # Six hours on, the sample timer fires: the frozen pair is no new sample.
    await _fire(hass, COP_SAMPLE_INTERVAL.total_seconds() + 10)
    assert _stamps(hass_storage, entry) == [read.isoformat()]
    _stale()

    # The page is reached again: fresh counters, the figures come back.
    HistoryPanel.blocked = set()
    await _fire(hass, INTERVAL + 10)
    assert HISTORY in panel.walked
    read_again = web.last_read(HEAT)
    assert read_again > read
    assert float(_state(hass, STARTS).state) == 8.0
    day = _state(hass, "cop_day")
    assert day.state == "unknown"
    assert day.attributes["skäl"] == "väntar på ett prov som är 20 till 30 timmar gammalt"
    week = _state(hass, "cop_week")
    assert "har inte lästs" not in week.attributes["skäl"]
    assert "av 7 dygn samlade" in week.attributes["skäl"]
    lifetime = _state(hass, "cop_lifetime")
    assert float(lifetime.state) == pytest.approx(22510 / 9120, abs=0.01)
    # The mean run is written on the Modbus round, so it follows the fresh
    # starts at the next poll, half a minute on.
    await _fire(hass, 31)
    mean = _state(hass, "mean_run_24h")
    assert float(mean.state) == 30.0
    assert mean.attributes["antal starter /24 h"] == 8
    assert mean.attributes["antal starter senast läst"] == read_again.isoformat(timespec="seconds")

    # The next sample bears the moment the counters were read, not the clock.
    # The page is put out of reach again so the harvest that fires with the
    # timer does not move the moment under the test.
    HistoryPanel.blocked = {HISTORY}
    await _fire(hass, COP_SAMPLE_INTERVAL.total_seconds() + 10)
    assert _stamps(hass_storage, entry) == [read.isoformat(), read_again.isoformat()]
