"""The display across a restart, under a real Home Assistant core (R5, R6, R34).

Set-up used to harvest the display inside itself, so every restart walked the
panel before the platforms existed and the display sensors stood unavailable
until the walk was done. Here VSH's seven pages are set up against a display
that answers, and the set-up is held to walking nothing: without a store the
first harvest follows a few seconds later on the coordinator's own clock,
with a recent store the sensors come up with the stored values and the first
harvest waits out the rest of the interval, and with an old store the values
are too old to show and the harvest is early. The store is written after a
harvest and goes with the entry. The diagnostic sensor gives the device's own
account of all this, and the page names a pump being retried.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_harvest.py
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    DeadModbus,
    FakePanel,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_menu import VSH  # noqa: E402

from unittest.mock import patch  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402
from homeassistant.util import dt as dt_util  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    async_fire_time_changed,
)

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
from custom_components.ctc_ecozenith.harvest import (  # noqa: E402
    MIN_FIRST_DELAY,
    SAVE_DELAY_SECONDS,
)

INTERVAL = 1800
HOME = 1
#: What every screen answers: 7.2 degrees, in tenths.
READING = 72


class LivePanel(FakePanel):
    """A display whose pages answer: VSH's seven, each one screen with one reading.

    Every walk to a page is written down, so a test can say that the set-up
    made none.
    """

    instances: list["LivePanel"] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.page = HOME
        self.visits: list[int] = []
        LivePanel.instances.append(self)

    async def async_current_page(self) -> int:
        return self.page

    async def async_goto_page(self, target: int, route=None) -> bool:
        self.page = target
        self.visits.append(target)
        return True

    async def async_vars(self, screen) -> list:
        return [READING]

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        self.page = target
        return True

    async def async_goto_home(self, hops: int = 6) -> int:
        self.page = HOME
        return HOME

    @property
    def walked(self) -> list[int]:
        """The pages a harvest walked to, the way back home aside."""
        return [page for page in self.visits if page != HOME]


@pytest.fixture(autouse=True)
def live_panel(stubs):
    """A display that answers, in place of the refusing one the shared stubs install."""
    LivePanel.instances.clear()
    with pytest.MonkeyPatch.context() as patching:
        import custom_components.ctc_ecozenith as integration

        patching.setattr(integration, "CtcWebClient", LivePanel)
        yield


async def _vsh_entry(hass) -> MockConfigEntry:
    """VSH's seven pages, identity complete, menu read by this version. Not yet added."""
    stored = pages_to_storage(VSH)
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


async def _set_up(hass, entry: MockConfigEntry) -> LivePanel:
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    (panel,) = LivePanel.instances
    return panel


def _display_key(entry) -> str:
    return f"{DOMAIN}_{entry.entry_id}_display"


def _prefill(hass_storage, entry, read, values: dict[str, float]) -> None:
    """A display store as the integration writes it, every value read at ``read``."""
    key = _display_key(entry)
    hass_storage[key] = {
        "version": 1,
        "minor_version": 1,
        "key": key,
        "data": {
            "values": values,
            "read_at": {key: read.isoformat() for key in values},
            "harvested_at": read.isoformat(),
            "consumption": None,
        },
    }


async def _fire(hass, seconds: float) -> None:
    """Fire the timers due within ``seconds`` and let the harvest run its course."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    for _ in range(300):
        await asyncio.sleep(0)
    await hass.async_block_till_done()


def _state(hass, key: str):
    return hass.states.get(_entity_id(hass, "sensor", key))


# ----------------------------------------------------------- no store


async def test_a_start_without_a_store_walks_nothing_and_harvests_soon_after(hass, hass_storage, stubs):
    entry = await _vsh_entry(hass)
    panel = await _set_up(hass, entry)
    assert panel.visits == [], "uppsättningen rör inte panelen"
    assert _state(hass, "p21_utetemperatur").state == "unavailable"
    assert _display_key(entry) not in hass_storage

    # A few seconds on, the first harvest walks every page and the sensors fill.
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert panel.walked == [page.page for page in VSH]
    state = _state(hass, "p21_utetemperatur")
    assert state.state == "7.2"
    assert "senast läst" in state.attributes
    read_at = dt_util.parse_datetime(state.attributes["senast läst"])
    assert read_at is not None and abs((dt_util.utcnow() - read_at).total_seconds()) < 60

    # And the harvest is written down, a moment later.
    await _fire(hass, SAVE_DELAY_SECONDS + 1)
    stored = hass_storage[_display_key(entry)]["data"]
    assert stored["values"]["p21_utetemperatur"] == 7.2
    # The attribute is cut to seconds; the store keeps the whole moment.
    assert stored["read_at"]["p21_utetemperatur"].startswith(read_at.isoformat()[:19])
    assert stored["harvested_at"]


# ------------------------------------------------------- a recent store


async def test_a_restart_within_the_interval_shows_the_stored_values_and_moves_nothing(
    hass, hass_storage, stubs
):
    read = (dt_util.utcnow() - timedelta(minutes=10)).replace(microsecond=0)
    entry = await _vsh_entry(hass)
    _prefill(hass_storage, entry, read, {"p21_utetemperatur": 7.2, "p22_utetemperatur": 8.1})
    panel = await _set_up(hass, entry)

    # The sensors come up with what was read before the restart, as old as it is.
    state = _state(hass, "p22_utetemperatur")
    assert state.state == "8.1"
    assert state.attributes["senast läst"] == read.isoformat()
    assert panel.visits == []

    # Not even a few seconds on: the first harvest is owed twenty minutes from now.
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert panel.visits == []
    assert _state(hass, "p22_utetemperatur").state == "8.1"

    # When it is owed, it walks, and the readings are fresh again.
    await _fire(hass, 21 * 60)
    assert panel.walked == [page.page for page in VSH]
    state = _state(hass, "p22_utetemperatur")
    assert state.state == "7.2"
    assert state.attributes["senast läst"] > read.isoformat()


# ---------------------------------------------------------- an old store


async def test_an_old_store_shows_nothing_as_now_and_harvests_early(hass, hass_storage, stubs):
    read = (dt_util.utcnow() - timedelta(days=2)).replace(microsecond=0)
    entry = await _vsh_entry(hass)
    _prefill(hass_storage, entry, read, {"p21_utetemperatur": 9.9})
    panel = await _set_up(hass, entry)

    # Two days old is not now: unavailable, and the panel still untouched.
    assert _state(hass, "p21_utetemperatur").state == "unavailable"
    assert panel.visits == []
    await _fire(hass, MIN_FIRST_DELAY + 1)
    assert panel.walked == [page.page for page in VSH]
    assert _state(hass, "p21_utetemperatur").state == "7.2"


# ------------------------------------------------------ the harvest sensor


async def test_the_harvest_sensor_accounts_for_the_walks(hass, hass_storage, stubs):
    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    sensor = _entity_id(hass, "sensor", "display_harvest")
    state = hass.states.get(sensor)
    # Nothing harvested yet: unknown, never unavailable, and the next attempt named.
    assert state.state == "unknown"
    assert state.attributes["device_class"] == "timestamp"
    assert state.attributes["misslyckade i rad"] == 0
    assert state.attributes["hoppade över i rad"] == 0
    assert state.attributes["senaste skäl"] is None
    next_attempt = dt_util.parse_datetime(state.attributes["nästa försök"])
    assert next_attempt is not None
    assert 0 <= (next_attempt - dt_util.utcnow()).total_seconds() <= MIN_FIRST_DELAY + 1
    from homeassistant.helpers import entity_registry as er

    assert er.async_get(hass).async_get(sensor).entity_category == er.EntityCategory.DIAGNOSTIC

    await _fire(hass, MIN_FIRST_DELAY + 1)
    state = hass.states.get(sensor)
    harvested = dt_util.parse_datetime(state.state)
    assert harvested is not None and abs((dt_util.utcnow() - harvested).total_seconds()) < 60
    assert state.attributes["sidor lästa"] == [page.page for page in VSH]
    assert state.attributes["sidor missade"] == []
    # The next attempt is an interval on, on the configured pace.
    next_attempt = dt_util.parse_datetime(state.attributes["nästa försök"])
    assert INTERVAL - 60 <= (next_attempt - dt_util.utcnow()).total_seconds() <= INTERVAL + 60


async def test_the_harvest_sensor_comes_up_with_the_stored_moment(hass, hass_storage, stubs):
    read = (dt_util.utcnow() - timedelta(minutes=10)).replace(microsecond=0)
    entry = await _vsh_entry(hass)
    _prefill(hass_storage, entry, read, {"p21_utetemperatur": 7.2})
    await _set_up(hass, entry)
    state = hass.states.get(_entity_id(hass, "sensor", "display_harvest"))
    assert dt_util.parse_datetime(state.state) == read


async def test_a_display_that_will_not_answer_is_shown_as_such(hass, hass_storage, stubs):
    """The refusing panel of the shared stubs: every question raises."""
    entry = await _vsh_entry(hass)
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", FakePanel):
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await _fire(hass, MIN_FIRST_DELAY + 1)
    sensor = _entity_id(hass, "sensor", "display_harvest")
    state = hass.states.get(sensor)
    assert state.state == "unknown", "ingen skörd har lyckats, men entiteten står kvar"
    assert state.attributes["misslyckade i rad"] == 1
    assert "could not read the panel state" in state.attributes["senaste skäl"]
    # The display sensors themselves are unavailable, as before.
    assert _state(hass, "p21_utetemperatur").state == "unavailable"


# ----------------------------------------------------------- the empty page


async def test_the_page_names_a_pump_being_retried_and_why(hass, hass_storage, stubs):
    from custom_components.ctc_ecozenith import dashboard

    entry = await _vsh_entry(hass)
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", DeadModbus):
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
    config = dashboard.build_config(hass)
    (view,) = config["views"]
    (section,) = view["sections"]
    note = section["cards"][-1]["content"]
    assert note.startswith(f"{entry.title} svarar inte just nu (")
    assert entry.reason in note
    assert "försöker igen" in note


# --------------------------------------------------------- the entry goes


async def test_removing_the_entry_takes_its_stores_with_it(hass, hass_storage, stubs):
    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    await _fire(hass, MIN_FIRST_DELAY + 1)
    await _fire(hass, SAVE_DELAY_SECONDS + 1)
    keys = {f"{DOMAIN}_{entry.entry_id}_{suffix}" for suffix in ("display", "seen", "cop")}
    for key in keys:
        hass_storage.setdefault(key, {"version": 1, "minor_version": 1, "key": key, "data": {}})
    assert keys <= set(hass_storage)
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not keys & set(hass_storage)
