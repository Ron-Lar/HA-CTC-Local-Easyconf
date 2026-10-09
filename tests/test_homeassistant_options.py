""""Read the menu again" from the options under a real Home Assistant core (R13).

A tick in "read the menu again" used to throw the rest of the form away, and
the three background tries at the menu were spent once per run, so a reading
ordered from the options after them could miss and nothing read the menu until
a restart. Here the flow is driven from the first form to the save: the fields
of the first form come through, the count starts over and the background reads
again after the save, a missed reading writes neither menu nor version, and a
busy panel changes nothing at all.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_options.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol  # noqa: E402  (comes with Home Assistant)

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    FakeModbus,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_menu import VSH, VSH_PAGES, _pages_in  # noqa: E402

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from homeassistant.loader import async_get_integration  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import (  # noqa: E402
    MenuReading,
    PanelBusy,
    pages_to_storage,
)
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_CHECK_UPDATES,
    CONF_ENABLE_CONTROL,
    CONF_FAST_INTERVAL,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_RESTORE_PAGE,
    CONF_SEND_STATISTICS,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_VISIT_SYSTEM_INFO,
    DOMAIN,
)

RESCAN = f"custom_components.{DOMAIN}.config_flow.async_rescan_pages"
FORGET = f"custom_components.{DOMAIN}.stats.async_forget_install"


async def _set_up_vsh(hass, **options):
    stored = pages_to_storage(VSH)
    return await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored, **options})


def _defaults(schema: vol.Schema) -> dict:
    """What a form offers, by field."""
    return {
        str(key.schema): key.default()
        for key in schema.schema
        if getattr(key, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }


async def _spend_the_tries(hass, entry) -> None:
    """Leave the entry a version behind with its three background tries spent."""
    integration._MENU_TRIES[entry.entry_id] = integration.MENU_READ_TRIES
    integration._MENU_LAST[entry.entry_id] = hass.loop.time()
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_MENU_VERSION: "0.9.0"}
    )
    await _let_the_background_run(hass)
    assert entry.state is ConfigEntryState.LOADED


async def test_a_tick_in_read_again_keeps_the_rest_of_the_form(hass, stubs):
    entry = await _set_up_vsh(hass)
    version = str((await async_get_integration(hass, DOMAIN)).version)
    whole = MenuReading(pages=VSH, complete=True)
    with (
        patch(RESCAN, AsyncMock(return_value=whole)) as rescan,
        patch(FORGET, AsyncMock()) as forget,
    ):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {
                "rescan": True,
                CONF_SLOW_PAGES: ["20", "25"],
                CONF_FAST_INTERVAL: 45,
                CONF_SLOW_INTERVAL: 900,
                CONF_RESTORE_PAGE: False,
                CONF_ENABLE_CONTROL: False,
                CONF_VISIT_SYSTEM_INFO: False,
                CONF_CHECK_UPDATES: False,
                CONF_SEND_STATISTICS: False,
            },
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "rescan"
        assert rescan.await_count == 1
        # The second form is offered as the first one left things.
        offered = _defaults(result["data_schema"])
        assert offered[CONF_SLOW_PAGES] == ["20", "25"]
        assert offered[CONF_SLOW_INTERVAL] == 900
        assert offered[CONF_RESTORE_PAGE] is False
        # Nothing is saved on the way: not the options, not the erasure.
        assert CONF_FAST_INTERVAL not in entry.options
        assert forget.await_count == 0
        assert len(FakeModbus.instances) == 1

        result = await hass.config_entries.options.async_configure(
            flow["flow_id"],
            {CONF_SLOW_PAGES: ["20", "25"], CONF_SLOW_INTERVAL: 900, CONF_RESTORE_PAGE: False},
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()
        assert forget.await_count == 1, "statistiken stängdes av: det sända raderas vid sparandet"

    options = entry.options
    assert options[CONF_FAST_INTERVAL] == 45
    assert options[CONF_SLOW_INTERVAL] == 900
    assert options[CONF_RESTORE_PAGE] is False
    assert options[CONF_ENABLE_CONTROL] is False
    assert options[CONF_VISIT_SYSTEM_INFO] is False
    assert options[CONF_CHECK_UPDATES] is False
    assert options[CONF_SEND_STATISTICS] is False
    assert _pages_in(entry, CONF_SLOW_PAGES) == [20, 25]
    assert _pages_in(entry, CONF_MENU) == VSH_PAGES
    assert options[CONF_MENU_VERSION] == version, "en hel läsning stämplar versionen"
    # And the reload took it all on.
    assert entry.state is ConfigEntryState.LOADED
    assert len(FakeModbus.instances) == 2
    assert entry.runtime_data.modbus.update_interval.total_seconds() == 45
    assert entry.runtime_data.control_enabled is False


async def test_read_again_after_the_tries_are_spent_starts_them_over_and_a_miss_stamps_nothing(
    hass, stubs
):
    entry = await _set_up_vsh(hass)
    await _spend_the_tries(hass, entry)
    assert stubs.discover.await_count == 0, "försöken är slut: bakgrunden läser inte"
    stored_menu = entry.options[CONF_MENU]

    with patch(RESCAN, AsyncMock(return_value=MenuReading())) as rescan:
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True}
        )
        assert result["type"] is FlowResultType.FORM
        assert rescan.await_count == 1
        # Offered from storage, and the count has started over.
        assert _defaults(result["data_schema"])[CONF_SLOW_PAGES] == [str(p) for p in VSH_PAGES]
        assert entry.entry_id not in integration._MENU_TRIES
        assert integration._MENU_LAST[entry.entry_id] == pytest.approx(hass.loop.time(), abs=5)

        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {CONF_SLOW_PAGES: ["20", "21"]}
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        await hass.async_block_till_done()

    # The missed reading wrote neither the menu nor the version; the tick
    # boxes, the person's own choice, were saved.
    assert entry.options[CONF_MENU] == stored_menu
    assert entry.options[CONF_MENU_VERSION] == "0.9.0"
    assert _pages_in(entry, CONF_SLOW_PAGES) == [20, 21]
    assert entry.state is ConfigEntryState.LOADED

    # The reload's background owes the menu again, and waits its pause first:
    # the form's own reading was the last attempt at the panel.
    await _let_the_background_run(hass)
    assert stubs.discover.await_count == 0
    await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
    assert stubs.discover.await_count == 1
    assert integration._MENU_TRIES[entry.entry_id] == 1


async def test_a_busy_panel_changes_nothing_and_keeps_the_count(hass, stubs):
    entry = await _set_up_vsh(hass)
    await _spend_the_tries(hass, entry)
    before = dict(entry.options)
    with patch(RESCAN, AsyncMock(side_effect=PanelBusy)):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True, CONF_FAST_INTERVAL: 45}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "panel_busy"
    await hass.async_block_till_done()
    assert dict(entry.options) == before, "inget har ändrats"
    assert integration._MENU_TRIES[entry.entry_id] == integration.MENU_READ_TRIES, (
        "ingen läsning skedde, så inget startar om"
    )
    assert len(FakeModbus.instances) == 2, "bara omladdningen som satte versionen tillbaka"
