"""A reading of the menu says whether it is the whole menu, and is folded in only then.

The sweep stops early on purpose when it loses the root, and gives a page up
when the route to it does not arrive. Both leave a list of pages that is true
as far as it goes and silent about the rest, and the readers used to take any
non empty list for the menu: merge_menu replaced the stored menu with it, the
tick boxes were filtered against it, and the version was stamped so nobody
read the menu again before the next release. On the i550 Pro the heat pump's
own page lies behind the second strip, so one interrupted re-read after an
upgrade would have taken the model, the control board's firmware and the
delivered heat with it. These tests hold a reading to saying when it is not
whole, hold the readers to never losing a stored page to such a reading, and
hold the sweep to keeping the home screen out of the menu.
"""

from __future__ import annotations

import asyncio
import pathlib

from test_menu_root import HOME, QUICK_MENU, SETTINGS_PARENTS, FakeMenu

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

#: VSH's menu as the i255 lays it out: the operation data root and six pages
#: one tap away, seven in all.
VSH_ROOT = 20
VSH_TABS = {VSH_ROOT: [21, 22, 23, 24, 25, 26]}
VSH_PAGES = [VSH_ROOT, 21, 22, 23, 24, 25, 26]


def run(coro):
    return asyncio.run(coro)


def page(const, number):
    return const.SlowPage(page=number, title=f"Sida {number}", screens=[number * 10])


def swept_halfway(web_api, catalogue):
    """VSH's seven pages, with the sweep losing the root on the fourth.

    Page 23 is a dialog as far as the panel is concerned: its back button does
    nothing and home cannot be found from it. The sweep has three pages and
    the root by then, and stops, as it should.
    """
    panel = FakeMenu(web_api, tabs=VSH_TABS, root=VSH_ROOT, start=VSH_ROOT, lost={23})
    reading = run(catalogue.async_discover_pages(panel, require_root=True))
    assert {p.page for p in reading.pages} == {20, 21, 22, 23}, "svepet avbröts halvvägs"
    return reading


# ----------------------------------------------- an interrupted sweep says so


def test_an_interrupted_sweep_is_not_the_whole_menu(catalogue, web_api):
    reading = swept_halfway(web_api, catalogue)
    assert not reading.complete
    assert reading.pages, "det som hittades kastas inte"


def test_a_whole_sweep_says_so_even_when_the_budget_runs_out(catalogue, web_api):
    # The tap budget is a fixed cap, spent the same way on the same menu every
    # time, so running out of it is not a sweep interrupted: the two houses'
    # menus have come back the same across many versions.
    panel = FakeMenu(web_api, tabs=VSH_TABS, root=VSH_ROOT, start=VSH_ROOT)
    reading = run(catalogue.async_discover_pages(panel))
    assert {p.page for p in reading.pages} == set(VSH_PAGES)
    assert reading.complete


# ------------------------------------- the form after "read the menu again"


def test_the_rescan_form_keeps_the_seven_pages_after_an_interrupted_sweep(
    catalogue, web_api, const
):
    stored = [page(const, n) for n in VSH_PAGES]
    reading = swept_halfway(web_api, catalogue)
    menu, fresh = catalogue.menu_after_rescan(stored, stored, reading.pages, reading.complete)
    assert [p.page for p in menu] == VSH_PAGES, "VSH:s sju sidor står kvar, i sin ordning"
    assert not fresh, "en halv läsning stämplar ingen version"
    # What the sweep did reach is offered with its fresh definitions.
    fresh_pages = {p.page: p for p in reading.pages}
    assert all(menu[i] is fresh_pages[p] for i, p in enumerate(VSH_PAGES) if p in fresh_pages)
    # And what it did not reach is the stored page, untouched.
    assert all(menu[i] is stored[i] for i, p in enumerate(VSH_PAGES) if p not in fresh_pages)


def test_an_interrupted_sweep_still_offers_its_pages_where_nothing_was_stored(
    catalogue, web_api
):
    # An installation whose first set-up found no pages at all has nothing to
    # lose, and what an interrupted sweep found is better than nothing.
    reading = swept_halfway(web_api, catalogue)
    menu, fresh = catalogue.menu_after_rescan([], [], reading.pages, reading.complete)
    assert [p.page for p in menu] == [20, 21, 22, 23]
    assert not fresh


def test_a_whole_rereading_replaces_the_menu_and_is_fresh(catalogue, const):
    stored = [page(const, n) for n in VSH_PAGES]
    discovered = [page(const, n) for n in VSH_PAGES + [27]]
    menu, fresh = catalogue.menu_after_rescan(stored, stored, discovered, True)
    assert [p.page for p in menu] == VSH_PAGES + [27]
    assert fresh


# ---------------------------------------- the re-read after an update, in code


def test_the_background_rereading_treats_an_incomplete_sweep_as_still_owed():
    # __init__.py imports Home Assistant and cannot be loaded here, so the
    # decision is read as source: an incomplete reading returns before anything
    # is folded in or stamped, exactly as an empty one does, and the counted
    # tries go on. The run under a real core is in test_homeassistant_menu.py.
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    body = source.split("async def _async_reread_menu")[1].split("\ndef ")[0]
    guard, folding = body.split("merge_menu(", 1)
    assert "not reading.complete" in guard, "en ofullständig läsning ska behandlas som skyldig"
    assert "return {}" in guard
    assert "CONF_MENU_VERSION: version" in folding, "bara en hel läsning stämplar versionen"


def test_the_first_set_up_stamps_the_version_only_for_a_whole_menu():
    # The first set-up offers whatever it got, one page without the root
    # included; Modbus alone is a good entry. But the stamp is what keeps the
    # background from reading the menu again, so it is left off a reading that
    # is not the whole menu, and the entry heals itself a few minutes in.
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    first = flow.split("async def async_step_slow")[1].split("async def async_step_dhcp")[0]
    assert "self._complete = reading.complete" in first
    stamped = first.split("if self._complete:")[1].split("return self.async_create_entry")[0]
    assert "CONF_MENU_VERSION" in stamped
    assert first.count("CONF_MENU_VERSION") == 1, "stämpeln sätts på ett enda ställe, bakom villkoret"


# -------------------------------------------- the home screen is not a page


def test_a_tap_that_lands_on_the_home_screen_does_not_make_it_a_page(catalogue, web_api):
    # A root with a control under the heading that leads home, and a home
    # screen with tiles of its own: Varmvatten and Värme, say. The home screen
    # was verified on the way in, so it is known, and it is neither collected
    # nor swept; its tiles are never pressed and the pages behind them never
    # enter the menu. Getting back from it is done through the operation data
    # tile, never with its top right button, which would open the quick menu.
    panel = FakeMenu(
        web_api,
        tabs={20: [HOME, 21], HOME: [30, 31]},
        root=20,
        start=HOME,
        parents={HOME: QUICK_MENU, QUICK_MENU: HOME},
    )
    reading = run(catalogue.async_discover_pages(panel))
    assert {p.page for p in reading.pages} == {20, 21}
    assert reading.complete
    assert not [what for what in panel.taps_on(HOME) if what.startswith("tab")], (
        "hemskärmens kakel trycks aldrig"
    )
    assert "back" not in panel.taps_on(HOME), "bakåtknappen trycks aldrig på hemskärmen"
    assert panel.taps_on(QUICK_MENU) == []
    assert {30, 31}.isdisjoint({p.page for p in reading.pages})


# ------------------------------------------------- what the restore presses


def test_the_restore_never_presses_the_home_screens_button(catalogue, web_api):
    # The panel started deep in the settings, on another branch than the
    # operation data menu. Stepping back from the root lands on the home
    # screen and no further: the top right button there opens the quick menu,
    # and the only known way in from home leads to the root, which is not
    # where the panel was. Home is where it stays.
    panel = FakeMenu(web_api, tabs={20: [21]}, root=20, start=386, parents=SETTINGS_PARENTS)
    reading = run(catalogue.async_discover_pages(panel))
    assert {p.page for p in reading.pages} == {20, 21}
    assert panel.page == HOME
    assert "back" not in panel.taps_on(HOME)
    assert panel.taps_on(QUICK_MENU) == []


def test_the_restore_goes_back_in_when_the_panel_started_on_the_root(catalogue, web_api):
    panel = FakeMenu(web_api, tabs={20: [21, 22]}, root=20, start=20)
    run(catalogue.async_discover_pages(panel))
    assert panel.page == 20


# ------------------------ the client remembers the home screen it recognised


class HomeScreenClient:
    """Builds a real CtcWebClient whose panel is simulated below the request layer.

    Page 1 is home and carries the operation data tile while ``tile_readable``
    is true; a slow text catalogue makes it unreadable. The top right button
    on home opens the quick menu, page 2, whose own button returns home. Page
    30 is a subpage whose button leads home.
    """

    def __new__(cls, web_api):
        client = web_api.CtcWebClient(session=None, host="192.0.2.10")
        client.page = 1
        client.tile_readable = True
        client.presses = []

        async def screen_map(refresh=False):
            return {1: [10], 2: [20], 30: [300]}

        async def current_page():
            return client.page

        async def operation_tile(page):
            if page == 1 and client.tile_readable:
                return 10, [10], (120, 150)
            return None

        async def click(screens, x, y):
            client.presses.append((client.page, x, y))
            client.page = {1: 2, 2: 1, 30: 1}[client.page]
            return []

        client.async_screen_map = screen_map
        client.async_current_page = current_page
        client._async_operation_tile = operation_tile
        client.async_click = click
        return client


def test_a_home_screen_recognised_once_is_not_pressed_when_its_tile_is_unreadable(web_api):
    client = HomeScreenClient(web_api)
    assert run(client.async_goto_home()) == 1
    assert client.presses == []
    # A slow minute: the caption cannot be read, but the page is the same.
    client.tile_readable = False
    assert run(client.async_goto_home()) == 1, "panelen är hemma, och sägs vara det"
    assert client.presses == [], "snabbmenyn öppnas inte"
    assert client.page == 1


def test_a_home_screen_never_recognised_is_still_searched_for(web_api):
    # Without a remembered home there is nothing to go by but the tile, and
    # the search steps back until it goes in circles: home, quick menu, home.
    client = HomeScreenClient(web_api)
    client.tile_readable = False
    assert run(client.async_goto_home()) is None
    assert [where for where, _x, _y in client.presses] == [1, 2]
    assert client.page == 1


def test_the_remembered_home_ends_the_search_from_a_subpage(web_api):
    client = HomeScreenClient(web_api)
    assert run(client.async_goto_home()) == 1
    client.page = 30
    client.tile_readable = False
    assert run(client.async_goto_home()) == 1
    assert [where for where, _x, _y in client.presses] == [30], "ett steg, inget på hemskärmen"
