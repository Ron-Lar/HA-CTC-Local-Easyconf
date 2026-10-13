"""The three actions under a real Home Assistant core (roadmap R76).

release lets go of the controls it is handed and nothing else, writing
nothing; reread_menu reads the menu the way the options form does, under the
runtime's one panel lock, and says at once when the panel is taken;
cop_sample writes the counters down now and answers with them. What can be
read without a core is in test_services.py.

Shares the stand-ins and fixtures of test_homeassistant.py, and the answering
history page of test_homeassistant_cop_freshness.py, and runs the same way,
from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_services.py
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.loader import async_get_integration
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakeModbus,
    _advance,
    _entity_id,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)
from test_homeassistant_cop_freshness import (
    CONSUMED,
    HEAT,
    HistoryPanel,
    _entry as _history_entry,
)
from test_homeassistant_harvest import LivePanel, _fire, _prefill
from test_homeassistant_menu import VSH

import custom_components.ctc_ecozenith as integration
from custom_components.ctc_ecozenith.catalogue import (
    MenuReading,
    pages_from_storage,
    pages_to_storage,
)
from custom_components.ctc_ecozenith.const import (
    CONF_DISPLAY,
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_ROOT,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    CONTROL_KEEPALIVE_SECONDS,
    DOMAIN,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY

#: Where the reading the actions make is patched in: catalogue's own walk,
#: which async_rescan_pages calls under the panel lock, as the options do.
DISCOVER = f"custom_components.{DOMAIN}.catalogue.async_discover_pages"


async def _call(hass, service: str, data: dict, **kwargs):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True, **kwargs)


def _held(entry) -> dict[int, int]:
    return entry.runtime_data.control.active


# ------------------------------------------------------------------ registered


async def test_the_three_actions_are_there_once_for_the_domain(hass):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    for service in ("release", "reread_menu", "cop_sample"):
        assert hass.services.has_service(DOMAIN, service), service
    # A reload is not a second registration, and the actions stay through it.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, "release")
    assert hass.services.supports_response(DOMAIN, "cop_sample") == "optional"


# --------------------------------------------------------------------- release


async def test_release_lets_go_of_one_control_and_keeps_the_rest_without_writing(hass):
    entry = await _set_up(hass)
    (client,) = FakeModbus.instances
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    speed = _entity_id(hass, "number", "ctl_max_rps")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": speed, "value": 60},
                                   blocking=True)
    assert client.writes == [(1010, 215), (1002, 600)]
    assert _held(entry) == {1010: 215, 1002: 600}

    await _call(hass, "release", {"entity_id": room})
    # Nothing written by the release, the room setpoint back on the pump's own
    # value, the speed limit still held.
    assert client.writes == [(1010, 215), (1002, 600)]
    assert _held(entry) == {1002: 600}
    state = hass.states.get(room)
    assert state.state == "20.5"
    assert state.attributes["styrning aktiv"] == "nej"
    assert hass.states.get(speed).attributes["styrning aktiv"] == "ja"

    # The keepalive goes on for what is held, and only for that.
    await _advance(hass, CONTROL_KEEPALIVE_SECONDS + 1)
    assert client.writes == [(1010, 215), (1002, 600), (1002, 600)]
    assert all(1000 <= address < 1100 for address, _ in client.writes)


async def test_release_takes_a_select_and_a_list_of_entities(hass):
    entry = await _set_up(hass)
    mode = _entity_id(hass, "select", "ctl_dhw_mode")
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    await hass.services.async_call("select", "select_option",
                                   {"entity_id": mode, "option": "Komfort"}, blocking=True)
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 22},
                                   blocking=True)
    assert set(_held(entry)) == {1007, 1010}
    await _call(hass, "release", {"entity_id": [mode, room]})
    assert _held(entry) == {}
    # Nothing held, so nothing is kept alive either.
    (client,) = FakeModbus.instances
    written = list(client.writes)
    await _advance(hass, 2 * CONTROL_KEEPALIVE_SECONDS + 2)
    assert client.writes == written


async def test_release_refuses_what_is_not_a_control_and_then_releases_nothing(hass):
    entry = await _set_up(hass)
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    await hass.services.async_call("number", "set_value", {"entity_id": room, "value": 21.5},
                                   blocking=True)
    outdoor = _entity_id(hass, "sensor", "outdoor_temp")
    for wrong in (outdoor, "number.not_there", "light.kitchen"):
        with pytest.raises(ServiceValidationError) as refused:
            await _call(hass, "release", {"entity_id": [room, wrong]})
        assert refused.value.translation_key == "not_a_control"
        assert refused.value.translation_placeholders == {"entity_id": wrong}
    # The control named beside the wrong one is still held.
    assert _held(entry) == {1010: 215}


async def test_release_refuses_a_control_of_an_entry_that_is_not_loaded(hass):
    entry = await _set_up(hass)
    room = _entity_id(hass, "number", "ctl_room_setpoint_1")
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as refused:
        await _call(hass, "release", {"entity_id": room})
    assert refused.value.translation_key == "not_a_control"


# ----------------------------------------------------------------- reread_menu


def _whole(pages) -> MenuReading:
    return MenuReading(pages=pages, complete=True, root=20, home_found=True,
                       tile_found=True, root_entered=True)


async def test_reread_menu_reads_folds_in_and_reloads(hass):
    """A whole reading is folded in by the background's rule and stamped."""
    stored = pages_to_storage(VSH[:3])
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored[:2]})
    integration._MENU_TRIES[entry.entry_id] = 3  # the background had given up
    version = str((await async_get_integration(hass, DOMAIN)).version)
    discover = AsyncMock(return_value=_whole(VSH))
    with patch(DISCOVER, discover):
        await _call(hass, "reread_menu", {"config_entry_id": entry.entry_id})
        await hass.async_block_till_done()
    discover.assert_awaited_once()
    assert discover.await_args.kwargs == {"require_root": True}
    # The whole menu, the page switched off still off and the new ones on.
    menu = [page.page for page in pages_from_storage(entry.options[CONF_MENU])]
    chosen = [page.page for page in pages_from_storage(entry.options[CONF_SLOW_PAGES])]
    assert menu == [page.page for page in VSH]
    assert VSH[2].page not in chosen
    assert set(chosen) == {page.page for page in VSH} - {VSH[2].page}
    assert entry.options[CONF_MENU_VERSION] == version
    assert entry.options[CONF_MENU_ROOT] == 20
    # The write reloaded the entry, and the tries start over.
    assert entry.state is ConfigEntryState.LOADED
    assert entry.entry_id not in integration._MENU_TRIES
    assert integration._MENU_OUTCOME[entry.entry_id]["complete"] is True


async def test_reread_menu_says_the_panel_is_taken_and_waits_for_nothing(hass):
    stored = pages_to_storage(VSH[:3])
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    integration._MENU_TRIES[entry.entry_id] = 3
    before = dict(entry.options)
    discover = AsyncMock(return_value=_whole(VSH))
    panel = entry.runtime_data.web_client.panel
    await panel.acquire()  # a harvest walking the panel
    try:
        with patch(DISCOVER, discover), pytest.raises(HomeAssistantError) as failed:
            await _call(hass, "reread_menu", {})
    finally:
        panel.release()
    assert failed.value.translation_key == "panel_busy"
    assert failed.value.translation_placeholders == {"entries": entry.title}
    discover.assert_not_awaited()
    assert dict(entry.options) == before
    # Nothing was read, so the background's count stands as it was.
    assert integration._MENU_TRIES[entry.entry_id] == 3


async def test_reread_menu_that_misses_writes_nothing_and_says_so(hass):
    stored = pages_to_storage(VSH[:3])
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    integration._MENU_TRIES[entry.entry_id] = 3
    before = dict(entry.options)
    interrupted = MenuReading(pages=VSH[:1], complete=False, home_found=True,
                              tile_found=True, root_entered=True)
    for reading in (interrupted, MenuReading(home_found=False)):
        with (
            patch(DISCOVER, AsyncMock(return_value=reading)),
            pytest.raises(HomeAssistantError) as failed,
        ):
            await _call(hass, "reread_menu", {"config_entry_id": entry.entry_id})
        assert failed.value.translation_key == "menu_not_read"
        assert dict(entry.options) == before
    # Asked for now, so the tries the background counts start over.
    assert entry.entry_id not in integration._MENU_TRIES


async def test_reread_menu_refuses_an_entry_on_modbus_alone_and_one_that_is_not_there(hass):
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
            CONF_DISPLAY: False,
        },
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    discover = AsyncMock(return_value=_whole(VSH))
    with patch(DISCOVER, discover):
        with pytest.raises(ServiceValidationError) as refused:
            await _call(hass, "reread_menu", {"config_entry_id": entry.entry_id})
        assert refused.value.translation_key == "no_display"
        with pytest.raises(ServiceValidationError) as refused:
            await _call(hass, "reread_menu", {})
        assert refused.value.translation_key == "no_display_loaded"
        with pytest.raises(ServiceValidationError) as refused:
            await _call(hass, "reread_menu", {"config_entry_id": "not-an-entry"})
        assert refused.value.translation_key == "unknown_entry"
    discover.assert_not_awaited()


async def test_reread_menu_refuses_an_entry_that_is_not_loaded(hass):
    entry = await _set_up(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with pytest.raises(ServiceValidationError) as refused:
        await _call(hass, "reread_menu", {"config_entry_id": entry.entry_id})
    assert refused.value.translation_key == "entry_not_loaded"


# ------------------------------------- reread_menu across a reload and a save


def _held_walk(gate: asyncio.Event, reading: MenuReading):
    """A walk through the menu that lasts until the test lets it end."""

    async def walk(client, require_root=False):
        await gate.wait()
        return reading

    return walk


def _start(hass, entry) -> asyncio.Future:
    """The action for one entry, running beside the test.

    Not a task of Home Assistant's, so waiting for the core's tasks does not
    wait for the action, which is held on the panel lock in these tests.
    """
    return asyncio.ensure_future(_call(hass, "reread_menu", {"config_entry_id": entry.entry_id}))


@pytest.fixture
def live_panel(stubs, monkeypatch):
    """A display that answers, as the harvest tests have it."""
    LivePanel.instances.clear()
    monkeypatch.setattr(integration, "CtcWebClient", LivePanel)
    yield


async def test_a_reload_while_the_action_walks_brings_no_second_walker_and_no_write(
    hass, live_panel
):
    stored = pages_to_storage(VSH)
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    (first,) = LivePanel.instances
    before = dict(entry.options)
    gate = asyncio.Event()
    with patch(DISCOVER, _held_walk(gate, _whole(VSH))):
        action = _start(hass, entry)
        await _let_the_background_run(hass)
        assert first.panel.locked()

        # The entry reloads while the action walks: a save, a new address,
        # entities switched on. The new runtime's client has the entry's one
        # lock, the one the walk on the old client holds.
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        second = LivePanel.instances[-1]
        assert second is not first
        assert second.panel is first.panel

        # The new runtime's first harvest falls due while the action still
        # walks: it waits rather than walking the same panel beside it.
        await _fire(hass, MIN_FIRST_DELAY + 1)
        assert second.visits == []

        gate.set()
        with pytest.raises(HomeAssistantError) as failed:
            await action
    # Nothing written to an entry that was reloaded beneath the walk.
    assert failed.value.translation_key == "menu_not_read"
    assert failed.value.translation_placeholders == {"entries": entry.title}
    await _fire(hass, 1)
    assert dict(entry.options) == before
    # The harvest walks once the action has let go, and the old client never.
    assert second.walked == [page.page for page in VSH]
    assert first.visits == []


async def test_a_save_in_configure_while_the_write_waits_stands_and_the_action_writes_nothing(hass):
    stored = pages_to_storage(VSH[:3])
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    panel = entry.runtime_data.web_client.panel
    gate = asyncio.Event()
    harvested = asyncio.Event()

    async def harvest() -> None:
        async with panel:
            await harvested.wait()

    with patch(DISCOVER, _held_walk(gate, _whole(VSH))):
        action = _start(hass, entry)
        await _let_the_background_run(hass)
        # A harvest queues on the panel while the menu is read, and gets it
        # when the walk is done: the action's write waits behind it.
        queued = asyncio.ensure_future(harvest())
        await _let_the_background_run(hass)
        gate.set()
        await _let_the_background_run(hass)
        assert panel.locked() and not action.done()

        # The owner saves a new interval in Configure meanwhile.
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_SLOW_INTERVAL: 900}
        )
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED

        harvested.set()
        with pytest.raises(HomeAssistantError) as failed:
            await action
        await queued
    await hass.async_block_till_done()
    assert failed.value.translation_key == "menu_not_read"
    # The save stands, and the menu the action read was not written over it.
    assert entry.options[CONF_SLOW_INTERVAL] == 900
    assert [page.page for page in pages_from_storage(entry.options[CONF_MENU])] == [
        page.page for page in VSH[:3]
    ]
    assert CONF_MENU_ROOT not in entry.options


async def test_an_identity_written_while_the_write_waits_stands_beside_the_menu(hass):
    """The options are worked out at the write, not before the wait for the panel."""
    stored = pages_to_storage(VSH[:3])
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    panel = entry.runtime_data.web_client.panel
    runtime = entry.runtime_data
    gate = asyncio.Event()
    harvested = asyncio.Event()

    async def harvest() -> None:
        async with panel:
            await harvested.wait()

    with patch(DISCOVER, _held_walk(gate, _whole(VSH))):
        action = _start(hass, entry)
        await _let_the_background_run(hass)
        queued = asyncio.ensure_future(harvest())
        await _let_the_background_run(hass)
        gate.set()
        await _let_the_background_run(hass)
        assert not action.done()

        # The display's firmware, read in the background while the write
        # waits: written in place, without a reload.
        updated = {**IDENTITY, "display_firmware": "2026-09-01"}
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, CONF_IDENTITY: updated}
        )
        await hass.async_block_till_done()
        assert entry.runtime_data is runtime

        harvested.set()
        await action
        await queued
    await hass.async_block_till_done()
    assert [page.page for page in pages_from_storage(entry.options[CONF_MENU])] == [
        page.page for page in VSH
    ]
    assert entry.options[CONF_IDENTITY] == updated
    assert entry.state is ConfigEntryState.LOADED


# --------------------------------------------------- reread_menu over two pumps

#: A second unit, at another documentation address and with a MAC of its own.
OTHER_HOST = "192.0.2.56"


async def _set_up_another(hass) -> MockConfigEntry:
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{OTHER_HOST}",
        title=f"{MODEL} ({OTHER_HOST})",
        data={
            CONF_HOST: OTHER_HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={
            CONF_MENU_VERSION: version,
            CONF_IDENTITY: {**IDENTITY, "mac": "02:00:00:00:00:02"},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_reread_menu_over_two_pumps_names_the_one_read_and_the_one_taken(hass):
    version = str((await async_get_integration(hass, DOMAIN)).version)
    first = await _set_up(hass)
    second = await _set_up_another(hass)
    taken = second.runtime_data.web_client.panel
    assert taken is not first.runtime_data.web_client.panel, "ett lås per post"
    discover = AsyncMock(return_value=_whole(VSH))
    await taken.acquire()  # a harvest walking the second panel
    try:
        with patch(DISCOVER, discover), pytest.raises(HomeAssistantError) as failed:
            await _call(hass, "reread_menu", {})
    finally:
        taken.release()
    await hass.async_block_till_done()
    # The first was read whole and written, so the answer says so instead of
    # that nothing has changed, and names the taken one beside it.
    assert failed.value.translation_key == "menu_partly_read"
    assert failed.value.translation_placeholders == {
        "read": first.title,
        "busy": second.title,
        "missed": "0",
    }
    discover.assert_awaited_once()
    assert first.options[CONF_MENU_VERSION] == version
    assert first.options[CONF_MENU_ROOT] == 20
    assert CONF_MENU_ROOT not in second.options
    assert first.state is ConfigEntryState.LOADED


async def test_reread_menu_over_two_pumps_with_none_read_names_the_missed_and_the_taken(hass):
    first = await _set_up(hass)
    second = await _set_up_another(hass)
    before = dict(first.options)
    taken = second.runtime_data.web_client.panel
    await taken.acquire()
    try:
        with patch(DISCOVER, AsyncMock(return_value=MenuReading(home_found=False))), pytest.raises(
            HomeAssistantError
        ) as failed:
            await _call(hass, "reread_menu", {})
    finally:
        taken.release()
    # Both are named: a taken panel is no reason to keep quiet about a miss.
    assert failed.value.translation_key == "menu_busy_and_not_read"
    assert failed.value.translation_placeholders == {"busy": second.title, "missed": first.title}
    assert dict(first.options) == before


# ------------------------------------------------------------------ cop_sample


@pytest.fixture
def history_page(stubs, monkeypatch):
    """The answering display with the history page, as the freshness tests have it."""
    LivePanel.instances.clear()
    HistoryPanel.blocked = set()
    monkeypatch.setattr(integration, "CtcWebClient", HistoryPanel)
    yield


async def test_cop_sample_takes_the_fresh_counters_once_and_answers_with_them(
    hass, hass_storage, history_page
):
    read = (dt_util.utcnow() - timedelta(days=2)).replace(microsecond=0)
    entry = await _history_entry(hass)
    _prefill(hass_storage, entry, read, {HEAT: 22499.0, CONSUMED: 9116.0, "p21_utetemperatur": 7.2})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    store = f"{DOMAIN}_{entry.entry_id}_cop"

    def stamps() -> list[str]:
        return [row[0] for row in hass_storage[store]["data"]["recent"]]

    # The stored pair at start-up is a sample at its own moment.
    assert stamps() == [read.isoformat()]
    # The pair standing from the store is that same reading: nothing new to take.
    answer = await _call(hass, "cop_sample", {"config_entry_id": entry.entry_id},
                         return_response=True)
    assert answer[entry.entry_id]["recorded"] is False
    assert stamps() == [read.isoformat()]

    # The first harvest reads the history page: a fresh pair, taken at once
    # rather than at the six hour timer, and answered with.
    await _fire(hass, MIN_FIRST_DELAY + 1)
    read_again = entry.runtime_data.web.last_read(HEAT)
    assert read_again > read
    answer = await _call(hass, "cop_sample", {}, return_response=True)
    assert answer == {
        entry.entry_id: {
            "recorded": True,
            "heat_out_kwh": 22510.0,
            "energy_in_kwh": 9120.0,
            "energy_in_from": "display",
            "read_at": read_again.isoformat(timespec="seconds"),
            "last_sample_at": read_again.isoformat(timespec="seconds"),
        }
    }
    assert stamps() == [read.isoformat(), read_again.isoformat()]
    # Asked again before the page is read again: the same reading, kept once.
    answer = await _call(hass, "cop_sample", {"config_entry_id": entry.entry_id},
                         return_response=True)
    assert answer[entry.entry_id]["recorded"] is False
    assert stamps() == [read.isoformat(), read_again.isoformat()]
    # Called without asking for the answer, it still takes the sample.
    assert await _call(hass, "cop_sample", {}) is None


async def test_cop_sample_refuses_a_heat_pump_without_the_counters(hass):
    entry = await _set_up(hass)
    with pytest.raises(ServiceValidationError) as refused:
        await _call(hass, "cop_sample", {"config_entry_id": entry.entry_id}, return_response=True)
    assert refused.value.translation_key == "no_counters"
    assert refused.value.translation_placeholders == {"entry": entry.title}
    with pytest.raises(ServiceValidationError) as refused:
        await _call(hass, "cop_sample", {}, return_response=True)
    assert refused.value.translation_key == "no_counters_loaded"
    await _let_the_background_run(hass)
