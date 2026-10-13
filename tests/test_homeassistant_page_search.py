""""Search for the way to a page" from Configure, under a real Home Assistant core (L7).

The search itself is held in test_page_search.py over the fake display. Here
the whole flow runs, from the tick box on the first form to the save, with
the entry's own client, a real CtcWebClient, talking to that fake display:
an i550 Pro whose root spends the menu reading's budget before Kompressor
VP1, the way PT's does. The form offers the page the panel shows, the search
runs only when the form is submitted, finds the page, shows it, and the save
puts it in the menu, ticked, with the rest of the first form and without the
version stamp. A search that misses says so on the form and saves nothing, a
busy panel is said at once, and the two walks are never ticked together.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_page_search.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
import voluptuous as vol  # comes with Home Assistant

from fake_panel import BACK, centre
from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)
from test_menu_sweep import HOME, I550_ROOT, VP1, VP_TABS, i550
from test_page_search import MENU, VP_AT, pt_like

from custom_components.ctc_ecozenith import catalogue, web_api
from custom_components.ctc_ecozenith.catalogue import pages_from_storage, pages_to_storage
from custom_components.ctc_ecozenith.const import (
    CONF_DISPLAY,
    CONF_MENU,
    CONF_MENU_ROOT,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_SLOW_INTERVAL,
    CONF_SLOW_PAGES,
    CONF_WEB_PORT,
    DOMAIN,
)

#: The tick box on the first form and the field on the next, as the flow names them.
FIND_PAGE, PAGE = "find_page", "page"


def _fields(schema: vol.Schema) -> list[str]:
    return [str(key.schema) for key in schema.schema]


def _defaults(schema: vol.Schema) -> dict:
    return {
        str(key.schema): key.default()
        for key in schema.schema
        if getattr(key, "default", vol.UNDEFINED) is not vol.UNDEFINED
    }


@pytest.fixture(name="display")
def display_fixture():
    """Hand the entry's client the fake display, and keep the first harvest out of the way.

    Every client the entry builds, the one set-up makes and the one after the
    save's reload, is a real CtcWebClient on the panel the test puts here.
    The first harvest is pushed far off, so the only walk on the panel is the
    search the test starts.
    """
    holder: dict = {}

    def client(session, host, port=80, language=1):
        return web_api.CtcWebClient(holder["panel"].session, host, port, language)

    with (
        patch(f"custom_components.{DOMAIN}.CtcWebClient", client),
        patch(f"custom_components.{DOMAIN}.harvest.MIN_FIRST_DELAY", 10**6),
    ):
        yield holder


async def _menu_of(panel) -> tuple[list, int | None]:
    """The menu as a whole reading of it finds it on ``panel``."""
    reading = await catalogue.async_discover_pages(panel.client(web_api))
    assert reading.complete
    return reading.pages, reading.root


async def _set_up_i550(hass, display, start: int = VP1, menu_from=pt_like, **panel_options):
    """An entry with the menu the reading found; its panel stands on ``start``."""
    pages, root = await _menu_of(menu_from())
    stored = pages_to_storage(pages)
    display["panel"] = pt_like(start=start, **panel_options)
    entry = await _set_up(
        hass, **{CONF_MENU: stored, CONF_SLOW_PAGES: stored, CONF_MENU_ROOT: root}
    )
    assert entry.state is ConfigEntryState.LOADED
    return entry, display["panel"]


async def _tick_find_page(hass, entry, **first_form):
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert flow["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {FIND_PAGE: True, **first_form}
    )
    return flow["flow_id"], result


def _no_forbidden_press(panel) -> None:
    assert all((p.x, p.y) == BACK for p in panel.presses if p.y < 45)
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == []
    assert all(press.aimed for press in panel.presses)
    assert set(panel.shown) <= MENU | {HOME}


async def test_the_search_finds_kompressor_vp1_and_the_save_adds_it_ticked(hass, stubs, display):
    # The owner has walked the panel to Kompressor VP1 by hand, which the
    # menu reading never reaches on this root.
    entry, panel = await _set_up_i550(hass, display, start=VP1)
    before_menu = pages_from_storage(entry.options[CONF_MENU])
    before_selection = [page.page for page in pages_from_storage(entry.options[CONF_SLOW_PAGES])]
    version = entry.options[CONF_MENU_VERSION]
    assert VP1 not in {page.page for page in before_menu}

    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert FIND_PAGE in _fields(flow["data_schema"])
    assert _defaults(flow["data_schema"])[FIND_PAGE] is False, "aldrig av sig självt"
    flow_id, result = await _tick_find_page(hass, entry, **{CONF_SLOW_INTERVAL: 900})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "find_page"
    # The page the panel shows, read without a press.
    assert _defaults(result["data_schema"])[PAGE] == VP1
    assert panel.presses == []
    assert result["description_placeholders"] == {
        "taps": "40", "depth": "3", "screen_map": f"http://{HOST}/sm/all",
    }

    result = await hass.config_entries.options.async_configure(flow_id, {PAGE: VP1})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "find_page_found"
    assert result["description_placeholders"] == {
        "page": "42", "title": "Kompressor VP1", "count": "2", "steps": "2", "tries": "23",
    }
    # The search has run and the panel is back on the page it was on, and
    # nothing has been written yet.
    assert panel.page == VP1
    assert pages_from_storage(entry.options[CONF_MENU]) == before_menu
    _no_forbidden_press(panel)
    presses = len(panel.presses)

    result = await hass.config_entries.options.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert len(panel.presses) == presses, "sparandet trycker ingenting"

    menu = pages_from_storage(entry.options[CONF_MENU])
    assert [page.page for page in menu] == [*(page.page for page in before_menu), VP1]
    (vp1,) = [page for page in menu if page.page == VP1]
    assert vp1.route == [centre(VP_AT), centre(VP_TABS[0])]
    assert vp1.title == "Kompressor VP1"
    assert [value.key for value in vp1.values] == ["p42_s138_v118", "p42_s138_v124"]
    selection = [page.page for page in pages_from_storage(entry.options[CONF_SLOW_PAGES])]
    assert selection == [*before_selection, VP1]
    # The rest of the first form is saved with it, and the version stamp is
    # left alone: one page is not a reading of the menu.
    assert entry.options[CONF_SLOW_INTERVAL] == 900
    assert entry.options[CONF_MENU_VERSION] == version
    assert entry.options[CONF_MENU_ROOT] == I550_ROOT
    assert entry.state is ConfigEntryState.LOADED


async def test_a_page_the_menu_had_is_replaced_where_it_stood(hass, stubs, display):
    # A menu read where the heat pump was drawn the other way has Kompressor
    # VP1 by another route; the search's route takes its place, in its
    # place, with the same keys, and the other pages are left as they were.
    entry, panel = await _set_up_i550(hass, display, start=HOME, menu_from=i550)
    before = pages_from_storage(entry.options[CONF_MENU])
    position = [page.page for page in before].index(VP1)
    flow_id, result = await _tick_find_page(hass, entry)
    result = await hass.config_entries.options.async_configure(flow_id, {PAGE: VP1})
    assert result["step_id"] == "find_page_found"
    result = await hass.config_entries.options.async_configure(flow_id, {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    menu = pages_from_storage(entry.options[CONF_MENU])
    assert [page.page for page in menu] == [page.page for page in before]
    assert menu[position].route == [centre(VP_AT), centre(VP_TABS[0])]
    assert [v.key for v in menu[position].values] == [v.key for v in before[position].values]
    assert [p for n, p in enumerate(menu) if n != position] == [
        p for n, p in enumerate(before) if n != position
    ]
    assert panel.page == HOME


async def test_a_search_that_misses_says_so_and_saves_nothing(hass, stubs, display):
    # The root does not draw the heat pump just now.
    entry, panel = await _set_up_i550(hass, display, start=HOME, vp_drawn=False)
    options = dict(entry.options)
    flow_id, result = await _tick_find_page(hass, entry, **{CONF_SLOW_INTERVAL: 900})
    result = await hass.config_entries.options.async_configure(flow_id, {PAGE: VP1})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "find_page"
    assert result["errors"] == {"base": "page_not_found"}
    # The number stays in the field, to try again in another operating state.
    assert _defaults(result["data_schema"])[PAGE] == VP1
    assert dict(entry.options) == options, "ingenting sparas"
    assert panel.page == HOME
    _no_forbidden_press(panel)


async def test_a_page_the_display_does_not_have_is_said_without_a_press(hass, stubs, display):
    entry, panel = await _set_up_i550(hass, display, start=HOME)
    flow_id, result = await _tick_find_page(hass, entry)
    result = await hass.config_entries.options.async_configure(flow_id, {PAGE: 999})
    assert result["errors"] == {"base": "page_unknown"}
    assert panel.presses == []


async def test_a_busy_panel_is_said_at_once_and_not_walked(hass, stubs, display):
    entry, panel = await _set_up_i550(hass, display, start=VP1)
    lock = entry.runtime_data.web_client.panel
    await lock.acquire()
    try:
        flow_id, result = await _tick_find_page(hass, entry)
        # While another walk holds the panel, the page it shows is that
        # walk's, so it is not offered.
        assert PAGE not in _defaults(result["data_schema"])
        calls = len(panel.session.calls)
        result = await hass.config_entries.options.async_configure(flow_id, {PAGE: VP1})
        assert result["errors"] == {"base": "panel_busy"}
        assert len(panel.session.calls) == calls, "inte en fråga till displayen"
    finally:
        lock.release()
    assert panel.presses == []


async def test_the_two_walks_are_never_ticked_together(hass, stubs, display):
    entry, panel = await _set_up_i550(hass, display, start=HOME)
    options = dict(entry.options)
    calls = len(panel.session.calls)
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        flow["flow_id"], {FIND_PAGE: True, "rescan": True}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "one_walk"
    assert dict(entry.options) == options
    assert len(panel.session.calls) == calls, "ingen av dem går"


async def test_an_entry_on_modbus_alone_is_not_offered_the_search(hass, stubs):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"EcoZenith ({HOST})",
        data={
            "host": HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": "EcoZenith",
            "settings_name": "",
            CONF_DISPLAY: False,
        },
        options={CONF_SLOW_PAGES: []},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    flow = await hass.config_entries.options.async_init(entry.entry_id)
    assert FIND_PAGE not in _fields(flow["data_schema"])
    assert "rescan" in _fields(flow["data_schema"])
