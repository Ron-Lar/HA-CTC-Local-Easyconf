"""How far the walk through the menu got, under a real Home Assistant core (L6).

The background reading after an update keeps the walk's steps for this run,
on the runtime and outside it, the daily report carries them, and the warning
that ends the tries says at which step the walk stopped. A walk the display
cut short still skips the rest of the round, as it did when the error was
raised from the walk itself, and a walk made from the options form replaces
what the report says. Shares the stand-ins and fixtures of
test_homeassistant.py and runs the same way, from a virtual environment that
has Home Assistant and pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_menu_outcome.py
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    IDENTITY,
    _advance,
    _let_the_background_run,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_menu import VSH  # noqa: E402

from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

import custom_components.ctc_ecozenith as integration  # noqa: E402
from custom_components.ctc_ecozenith.catalogue import (  # noqa: E402
    MenuReading,
    pages_to_storage,
)
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_IDENTITY,
    CONF_MENU,
    CONF_MENU_VERSION,
    CONF_SLOW_PAGES,
    CONF_VISIT_SYSTEM_INFO,
    DOMAIN,
)
from custom_components.ctc_ecozenith.identity import Identity  # noqa: E402

READ_IDENTITY = f"custom_components.{DOMAIN}.async_read_identity"
RESCAN = f"custom_components.{DOMAIN}.config_flow.async_rescan_pages"

#: The i360's walk as it may well stand: home found, no operation data tile on it.
NO_TILE = MenuReading(home_found=True, tile_found=False, root_entered=False)


async def _owing_the_menu(hass, **options):
    """An entry a version behind, with no menu stored and the walk to system information off."""
    return await _set_up(
        hass,
        **{CONF_MENU_VERSION: "0.9.0", CONF_VISIT_SYSTEM_INFO: False, **options},
    )


async def test_the_walk_is_kept_for_the_report_and_the_last_warning_says_where_it_stopped(
    hass, stubs, caplog
):
    stubs.discover.return_value = NO_TILE
    entry = await _owing_the_menu(hass)
    await _let_the_background_run(hass)

    kept = integration._MENU_OUTCOME[entry.entry_id]
    assert kept["home_found"] is True and kept["root_entered"] is False
    assert entry.runtime_data.menu_outcome is kept, "samma ordbok, inte en kopia"

    features = integration._stats_extra_for(hass, entry)["features"]
    assert features["menu_pages"] == 0
    assert features["menu_home"] is True
    assert features["menu_root"] is False
    assert features["pages"] == 0

    for _ in range(2):
        await _advance(hass, integration.MENU_READ_RETRY.total_seconds() + 60)
    assert stubs.discover.await_count == 3
    assert "in 3 attempts" in caplog.text
    assert "tile on it was not" in caplog.text, "varningen säger var vandringen stannade"


async def test_a_stored_menu_is_counted_whatever_the_walk(hass, stubs):
    stored = pages_to_storage(VSH)
    entry = await _set_up(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: []})
    features = integration._stats_extra_for(hass, entry)["features"]
    assert features["menu_pages"] == len(VSH)
    assert features["pages"] == 0
    # No walk in this run: the menu was read by this version already.
    assert "menu_home" not in features and "menu_root" not in features


async def test_a_walk_the_display_cut_short_skips_the_rest_of_the_round(hass, stubs):
    stubs.discover.return_value = MenuReading(
        home_found=True, tile_found=True, root_entered=True, error="/click/118 timed out"
    )
    incomplete = {k: v for k, v in IDENTITY.items() if not k.startswith("heatpump")}
    read_identity = AsyncMock(return_value=Identity())
    with patch(READ_IDENTITY, read_identity):
        entry = await _owing_the_menu(hass, **{CONF_IDENTITY: incomplete})
        await _let_the_background_run(hass)
    assert stubs.discover.await_count == 1
    assert integration._MENU_OUTCOME[entry.entry_id]["error"] == "/click/118 timed out"
    assert read_identity.await_count == 0, "resten av varvet väntar till nästa"


async def test_a_walk_from_the_options_replaces_what_the_report_says(hass, stubs):
    stubs.discover.return_value = NO_TILE
    stored = pages_to_storage(VSH)
    entry = await _owing_the_menu(hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored})
    await _let_the_background_run(hass)
    assert entry.runtime_data.menu_outcome["root_entered"] is False

    whole = MenuReading(
        pages=VSH, complete=True, home_found=True, tile_found=True, root_entered=True
    )
    with patch(RESCAN, AsyncMock(return_value=whole)):
        flow = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            flow["flow_id"], {"rescan": True}
        )
    assert result["type"] is FlowResultType.FORM
    # The loaded runtime sees it before anything is saved: the same dictionary.
    assert entry.runtime_data.menu_outcome["root_entered"] is True
    assert entry.runtime_data.menu_outcome["pages"] == len(VSH)
    assert integration._stats_extra_for(hass, entry)["features"]["menu_root"] is True
