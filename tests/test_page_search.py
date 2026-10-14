"""The search for one page the reading of the menu does not reach (roadmap L7).

page_search.async_search_page runs only when the owner starts it, with
somebody at the panel, and it moves the physical panel further than anything
but the reading of the menu: up to MAX_TAPS controls tried, up to MAX_DEPTH
presses from the operation data root. It is held here, over test_menu_sweep's
fake display and the real client, to what it promises:

* it finds Kompressor VP1 on a root built the way PT's spends the reading's
  budget, and fails to find it, saying so, when the root does not draw the
  tile that leads there, as PT's i550 Pro does in some operating states;
* the budget and the depth are the owner's, and it stops at either;
* nothing under the heading but the panel's own button, that never on the
  home screen, nothing on the home screen but the operation data tile, every
  press worked out from the page the panel shows;
* never a control whose caption names Avancerat, Service, Define or the
  language selection, never a press on a page whose heading names one, and
  nothing on a page outside the operation data menu but its own button;
* the panel put back where it was found, by the recorded route where that is
  the page the owner walked it to.

The flow in the options that starts it is held under a real Home Assistant
core in test_homeassistant_page_search.py.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re

import pytest

from conftest import COMPONENT, load
from fake_panel import BACK, FakePanel, Spec, caption, centre, header_icon, heading, reading
from test_menu_sweep import (
    HOME,
    HOT_WATER,
    I550_HISTORY,
    I550_ROOT,
    OUTSIDE_PAGES,
    OUTSIDE_PARENTS,
    OUTSIDE_SCREENS,
    QUICK,
    SCHEMATIC,
    SETTINGS,
    TANK,
    TEXTS,
    VP,
    VP1,
    VP_RUN,
    VP_TABS,
    exploring,
    home_screen,
    i550,
    i550_expected,
)

#: The menu a search may move the panel through, on the panels below.
MENU = {I550_ROOT, VP, VP1, VP_RUN, TANK, I550_HISTORY}

#: Captions for the controls and pages the search must stay out of.
SERVICE, DEFINE, LANGUAGE, ELSEWHERE, DIALOG = 600, 610, 620, 700, 77
FORBIDDEN_TEXTS = {
    2001: ("Service", "Service"),
    2002: ("Definiera", "Define"),
    2003: ("Språk", "Language"),
    2004: ("Definiera system", "Define system"),
}
PANEL_TEXTS = {**TEXTS, **FORBIDDEN_TEXTS}


@pytest.fixture(scope="module")
def page_search():
    return load("page_search")


def run(coro):
    return asyncio.run(coro)


def search(page_search, web_api, panel: FakePanel, target: int = VP1, **budget):
    return run(page_search.async_search_page(panel.client(web_api), target, **budget))


# ------------------------------------------------------------ a root like PT's


def dead_grid(count: int, top: int = 48, columns: int = 5, step: int = 95) -> list[Spec]:
    """Boxes that look like controls and lead nowhere, the way a schematic's do."""
    return [
        Spec(1, 5 + (n % columns) * step, top + (n // columns) * 38, step - 10, 30)
        for n in range(count)
    ]


#: The three ways off the root, along the bottom in three widths, so the
#: sweep does not take them for a tab strip and tries them last, after the
#: picture's boxes, as it tries the places on PT's schematic.
TANK_AT = Spec(1, 10, 205, 140, 30, target=TANK)
VP_AT = Spec(1, 170, 205, 130, 30, target=VP)
HISTORY_AT = Spec(3, 330, 205, 120, 30, text=1102, target=I550_HISTORY)
ROOT_ROW = [caption(949, 240), reading("%.1f", 1, y=240)]


def pt_like(
    start: int = HOME,
    vp_drawn: bool = True,
    dead: int = 20,
    tank_dead: int = 20,
    vp_page: list[Spec] | None = None,
    extra_root: tuple[Spec, ...] = (),
    extra_screens: dict | None = None,
    extra_pages: dict | None = None,
    extra_parents: dict | None = None,
    **panel,
) -> FakePanel:
    """An i550 Pro whose schematic root spends the reading's budget before the heat pump.

    Twenty boxes on the picture that lead nowhere, then the tank, the heat
    pump and the history along the bottom, and a row with the outdoor
    temperature: 25 places on the root. The tank's page has twenty more dead
    boxes. The reading of the menu spends 25 of its 26 tries on the root and
    the last on the tank, so it never reaches the heat pump's strip, which is
    where PT's Kompressor VP1 is missed. ``vp_drawn`` False is the root in
    an operating state that does not draw the heat pump.
    """
    header = header_icon(284, SETTINGS)
    vp_box = Spec(1, VP_AT.x, VP_AT.y, VP_AT.width, VP_AT.height, visible=vp_drawn, target=VP)
    base = i550()
    screens = {
        **base.screens,
        400: [
            SCHEMATIC, heading(1101), header, *dead_grid(dead),
            TANK_AT, vp_box, HISTORY_AT, *ROOT_ROW, *extra_root,
        ],
        430: [
            heading(1103), header, caption(1104, 55), reading("%.1f", 2, y=55),
            *dead_grid(tank_dead, top=90, columns=7, step=66),
        ],
        **({410: vp_page} if vp_page is not None else {}),
        **(extra_screens or {}),
    }
    pages = {**base.pages, **(extra_pages or {})}
    parent = {**base.parent, **(extra_parents or {})}
    return FakePanel(
        pages=pages, screens=screens, texts=PANEL_TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=start, **panel,
    )


def vp1_expected(const):
    """Kompressor VP1 as the reading of the menu describes it, route and all."""
    (row,) = [row for row in i550_expected(const) if row[0] == VP1]
    page, title, screens, _route, values = row
    return page, title, screens, [centre(VP_AT), centre(VP_TABS[0])], values


def described(page) -> tuple:
    return page.page, page.title, page.screens, page.route, page.values


# ------------------------------------------------------------- the decisions


def test_the_budget_and_the_depth_are_the_ones_the_owner_decided(page_search):
    assert page_search.MAX_TAPS == 40
    assert page_search.MAX_DEPTH == 3
    signature = inspect.signature(page_search.async_search_page)
    assert signature.parameters["max_taps"].default == page_search.MAX_TAPS
    assert signature.parameters["max_depth"].default == page_search.MAX_DEPTH


# ------------------------------------------------- the page the reading misses


def test_the_reading_of_the_menu_misses_kompressor_vp1_on_a_root_like_pts(catalogue, web_api):
    # So the panel below stands for PT: the heat pump's strip is never swept.
    panel = pt_like()
    reading_ = run(catalogue.async_discover_pages(panel.client(web_api)))
    assert reading_.complete
    assert VP1 not in {page.page for page in reading_.pages}
    assert [p for p in panel.control_presses if p.page == VP] == []


def test_the_search_finds_kompressor_vp1_and_reads_it_as_the_reading_would(
    page_search, web_api, const
):
    panel = pt_like()
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.FOUND and found.found
    assert described(found.page) == vp1_expected(const)
    assert found.root == I550_ROOT
    # The heat pump's page shares its strip with Kompressor VP1, so of the
    # pages one press from the root it is searched first, before the tank's
    # twenty dead boxes: the root's 25 places, then the strip's first tab.
    # Alike is no reason to leave the root's own places untried, since the
    # page asked for could lie one press from the root.
    assert found.tries == 26
    assert len(exploring(panel)) == found.tries
    assert [p for p in panel.control_presses if p.page == TANK] == []
    # The route leads there from the root, which is how the harvest goes.
    assert pt_like().press_at_root(found.page.route, I550_ROOT) == VP1
    assert panel.page == HOME, "panelen ställs tillbaka på hemskärmen"


def test_a_tile_the_root_does_not_draw_just_now_is_not_found_and_nothing_is_offered(
    page_search, web_api
):
    # PT's root draws the way to Kompressor VP1 only in some operating
    # states. Without it there is no way there within the budget: the search
    # says so and offers nothing, and the panel is put back.
    panel = pt_like(vp_drawn=False)
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.NOT_FOUND
    assert found.page is None and not found.found
    assert VP1 not in panel.shown and VP not in panel.shown
    # The root's 24 places that are drawn, then 16 of the tank's dead boxes:
    # the budget to the press, and not one press more.
    assert found.tries == 40
    assert len(exploring(panel)) == 40
    assert sum(1 for page, _x, _y in exploring(panel) if page == TANK) == 16
    assert panel.page == HOME


def test_the_page_found_outlives_the_next_reading_and_the_harvest_gives_it_up_gently(
    page_search, catalogue, web_api
):
    # What the search found is kept by the reading of the menu owed after an
    # update, which on this root never reaches it: the page is ticked, has
    # its route, and the display's screen map has it. And when the root does
    # not draw the heat pump at harvest time, the harvest's walk ends at the
    # first press that moves nothing, on the root, and presses on no further.
    found = search(page_search, web_api, pt_like())
    reading_ = run(catalogue.async_discover_pages(pt_like().client(web_api)))
    menu = [*reading_.pages, found.page]
    selected = [page.page for page in menu]
    kept, ticked = catalogue.merge_menu(
        menu, selected, reading_.pages, reading_.root, reading_.screen_pages
    )
    assert VP1 in {page.page for page in kept} and VP1 in ticked

    hidden = pt_like(start=I550_ROOT, vp_drawn=False)
    client = hidden.client(web_api)
    client.root = I550_ROOT
    assert not run(client.async_goto_page(VP1, found.page.route))
    last = hidden.presses[-1]
    assert (last.page, (last.x, last.y), last.landed) == (I550_ROOT, centre(VP_AT), I550_ROOT)
    assert [p for p in hidden.presses if (p.x, p.y) == centre(VP_AT)] == [last]
    assert VP not in hidden.shown and VP1 not in hidden.shown


# ------------------------------------------------------- the owner at the panel


def far_parent(start: int = HOME) -> FakePanel:
    """A heat pump page that looks nothing like Kompressor VP1: two big buttons, no strip.

    Without a hint the search goes breadth first, and the tank's forty dead
    boxes, found before the heat pump, spend the budget.
    """
    vp_page = [
        heading(1105), header_icon(284, SETTINGS),
        Spec(1, 40, 80, 180, 100, target=VP1), Spec(1, 260, 80, 180, 100, target=VP_RUN),
    ]
    panel = pt_like(start=start, dead=2, vp_page=vp_page)
    # Forty dead boxes on the tank's page, in eight columns from under the
    # heading, all inside the panel's 480 by 272.
    panel.screens[430] = [
        heading(1103), header_icon(284, SETTINGS), *dead_grid(40, top=48, columns=8, step=58),
    ]
    return panel


def test_from_the_page_itself_the_way_home_shows_the_way_there(page_search, web_api):
    # The owner has walked the panel to Kompressor VP1 by hand, so the form
    # offers its number. The walk home passes the heat pump page and the
    # root, and the heat pump page is searched as soon as a try on the root
    # leads there, before the tank found a try earlier: the root's two dead
    # boxes, the tank, the heat pump, then the first button under it.
    elsewhere = search(page_search, web_api, far_parent())
    assert elsewhere.reason == page_search.NOT_FOUND, "utan ledtråd tar tanken budgeten"
    assert elsewhere.tries == page_search.MAX_TAPS

    panel = far_parent(start=VP1)
    found = search(page_search, web_api, panel)
    assert found.found and found.tries == 5
    assert found.page.route == [centre(VP_AT), (130, 130)]
    assert [p for p in panel.control_presses if p.page == TANK] == []
    # It ends on the page the owner left it on.
    assert panel.page == VP1


#: The heat pump page with two big buttons: first to Värmepump drift, which
#: draws Kompressor VP1's strip, then to Kompressor VP1 itself.
TO_VP_RUN = Spec(1, 40, 80, 180, 100, target=VP_RUN)
TO_VP1 = Spec(1, 260, 80, 180, 100, target=VP1)


def test_a_sibling_alike_does_not_lengthen_the_route(page_search, web_api):
    # The owner types the number without walking there, so there is no
    # trail. Värmepump drift turns up first and shares Kompressor VP1's
    # strip, but alike is no reason to leave the heat pump page: its next
    # button leads to the page itself, two presses from the root, where a way
    # through the sibling would be three, a press more at every harvest and
    # a route that breaks whenever the sibling's strip is not drawn.
    vp_page = [heading(1105), header_icon(284, SETTINGS), TO_VP_RUN, TO_VP1]
    panel = pt_like(dead=2, vp_page=vp_page)
    found = search(page_search, web_api, panel)
    assert found.found
    assert found.page.route == [(235, 220), (350, 130)]
    assert found.page.route == [centre(VP_AT), centre(TO_VP1)]
    assert [p for p in panel.control_presses if p.page == VP_RUN] == []
    assert pt_like(dead=2, vp_page=vp_page).press_at_root(found.page.route, I550_ROOT) == VP1


def alike_below() -> FakePanel:
    """A root with two pages under it: the first leads to a page alike, the second to the page.

    Page 51 has one box, to page 53, which draws the strip page 54 draws;
    page 52 has one box, to page 54 itself. Both lie one press from the root
    and neither is alike, so the search takes them in the order found.
    """
    screens = {12: home_screen(532, 50), **OUTSIDE_SCREENS}
    pages = {HOME: [12], **OUTSIDE_PAGES}
    parent = {**OUTSIDE_PARENTS}
    strip = [Spec(1, 0, 222, 150, 45, target=54), Spec(1, 150, 222, 150, 45)]
    layout = {
        50: [caption(949, 55), reading("%.1f", 0, y=55),
             Spec(1, 20, 120, 100, 50, target=51), Spec(1, 200, 120, 100, 50, target=52)],
        51: [Spec(1, 20, 120, 100, 50, target=53)],
        52: [Spec(1, 300, 160, 100, 40, target=54)],
        53: strip,
        54: [caption(949, 55), reading("%.1f", 0, y=55), *strip],
    }
    up = {50: HOME, 51: 50, 52: 50, 53: 51, 54: 52}
    for page, widgets in layout.items():
        screens[page * 10] = [heading(1001), header_icon(284, SETTINGS), *widgets]
        pages[page] = [page * 10]
        parent[page] = up[page]
    return FakePanel(
        pages=pages, screens=screens, texts=PANEL_TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=HOME,
    )


def test_a_page_alike_further_down_waits_for_the_pages_nearer_the_root(page_search, web_api):
    # Page 53 is alike, two presses down; page 52, one press down and not
    # alike, is searched before it all the same, and leads to the page in
    # two presses where the way through 53 would be three.
    panel = alike_below()
    found = search(page_search, web_api, panel, target=54)
    assert found.found
    assert found.page.route == [(250, 145), (350, 180)]
    assert [p for p in panel.control_presses if p.page == 53] == []


def test_from_the_page_asked_for_the_walk_home_is_the_first_thing_pressed(page_search, web_api):
    # The panel stands on the page asked for, so the search steps back from
    # it to the home screen, noting the way, and ends on it again: the walk
    # home is its first presses, the panel's own button and nothing else,
    # and nothing is pressed after the page has been read.
    panel = pt_like(start=VP1)
    found = search(page_search, web_api, panel)
    assert found.found and panel.page == VP1
    assert [(p.page, p.took) for p in panel.presses[:3]] == [
        (VP1, "back"), (VP, "back"), (I550_ROOT, "back"),
    ]
    assert panel.presses[-1].landed == VP1


def test_a_page_the_search_reached_is_walked_back_to_by_its_route(page_search, web_api):
    # Started on the tank's page, which the search tried on its way and so
    # knows the way to: from Kompressor VP1 two steps back to the root, then
    # the press on the tank, and no press more.
    panel = pt_like(start=TANK)
    found = search(page_search, web_api, panel)
    assert found.found and panel.page == TANK
    assert [(p.page, (p.x, p.y)) for p in panel.presses[-3:]] == [
        (VP1, BACK), (VP, BACK), (I550_ROOT, centre(TANK_AT)),
    ]


def test_a_page_passed_on_the_way_up_is_not_left_for_the_root(page_search, web_api):
    # Started on the heat pump's page, right above Kompressor VP1: one press
    # of the panel's own button puts it back, not a walk up to the root and
    # down the route again.
    panel = pt_like(start=VP)
    found = search(page_search, web_api, panel)
    assert found.found and panel.page == VP
    assert [(p.page, (p.x, p.y), p.landed) for p in panel.presses[-2:]] == [
        (VP, centre(VP_TABS[0]), VP1), (VP1, BACK, VP),
    ]


def test_a_page_set_aside_for_one_below_it_is_left_by_the_rest_of_the_route(
    page_search, web_api
):
    # Three presses down, with the owner's panel on the page: the walk home
    # passes every page above it, so each page found on that way is searched
    # before the one above it, and from the page above the rest of the route
    # is one press, not a walk up to the root and down again. Four presses of
    # the panel's own button home, the tile, three tries on each of the three
    # pages, the button once on each of the two pages found on the way, and
    # one press to regain each of them.
    panel = chain(3, start=53)
    found = search(page_search, web_api, panel, target=53)
    assert found.found and found.tries == 9
    assert found.presses == 4 + 1 + 9 + 2 + 2
    assert panel.page == 53


#: Where the panel ends, by where it stood: back where it was on a page the
#: search knows the way to, the history page among them since the root's
#: every place is tried before the heat pump's strip; outside the menu, and
#: on a page it does not know the way to, on the home screen.
ENDS = {
    HOME: HOME, SETTINGS: HOME, QUICK: HOME, I550_ROOT: I550_ROOT, VP: VP, VP1: VP1,
    TANK: TANK, I550_HISTORY: I550_HISTORY,
}


@pytest.mark.parametrize("start", sorted(ENDS))
def test_the_panel_ends_where_it_was_or_on_the_home_screen(page_search, web_api, start):
    panel = pt_like(start=start)
    found = search(page_search, web_api, panel)
    assert found.found
    assert panel.page == ENDS[start]


def the_heat_pump_place_leads(panel: FakePanel, to: int) -> None:
    """The heat pump's place on the root leads to ``to``, as in another operating state.

    And the tank's page gets a box where Kompressor VP1's first tab is,
    leading out of the menu, so a step pressed blind on the tank would show.
    """
    panel.screens[400] = [
        Spec(1, s.x, s.y, s.width, s.height, visible=s.visible, target=to)
        if (s.x, s.y, s.target) == (VP_AT.x, VP_AT.y, VP)
        else s
        for s in panel.screens[400]
    ]
    panel.screens[430] = [*panel.screens[430], Spec(1, 0, 222, 150, 45, target=SETTINGS)]


@pytest.mark.parametrize("to", [TANK, HOME])
def test_a_step_of_the_way_back_that_lands_elsewhere_ends_it(
    page_search, web_api, monkeypatch, to
):
    # The search for Värmepump drift, started on Kompressor VP1, knows the
    # way back there: the heat pump's place on the root, then the strip's
    # first tab. When the restore comes the place leads elsewhere, to the
    # tank or to the home screen, and the tab is pressed on neither: the
    # landing is not the heat pump page the place led to, so the panel is
    # left on the home screen.
    panel = pt_like(start=VP1)
    real = page_search._async_restore
    begun: list[int] = []

    async def noting(*args, **kwargs):
        begun.append(len(panel.presses))
        the_heat_pump_place_leads(panel, to)
        return await real(*args, **kwargs)

    monkeypatch.setattr(page_search, "_async_restore", noting)
    found = run(page_search.async_search_page(panel.client(web_api), VP_RUN))
    assert found.found
    (start,) = begun
    restore = [(p.page, (p.x, p.y), p.landed) for p in panel.presses[start:]]
    assert (I550_ROOT, centre(VP_AT), to) in restore
    assert centre(VP_TABS[0]) not in [point for _page, point, _landed in restore]
    assert SETTINGS not in panel.shown
    tile_icon = panel.screens[11][0]
    assert {(p.x, p.y) for p in panel.presses if p.page == HOME} <= {centre(tile_icon)}
    assert panel.page == HOME


#: How the search regains Kompressor VP1, by where the panel stands: from
#: the root by the rest of its route, from the tank by the panel's own button
#: up to the root and the route from there (catalogue._async_put_back).
REGAIN_FROM = {I550_ROOT: [], TANK: [(TANK, BACK, I550_ROOT)]}


@pytest.mark.parametrize("to", [TANK, HOME])
@pytest.mark.parametrize("start", sorted(REGAIN_FROM))
def test_a_page_regained_is_walked_to_with_every_step_held_to_the_page_it_led_to(
    page_search, web_api, start, to
):
    # The first step of Kompressor VP1's route lands elsewhere just now, on
    # the tank or the home screen, and the second is not pressed there: the
    # search knows the heat pump page is where that step led, and the walk
    # is astray at once.
    panel = pt_like(start=start)
    the_heat_pump_place_leads(panel, to)
    client = panel.client(web_api)
    page_map = run(client.async_screen_map())
    route = [centre(VP_AT), centre(VP_TABS[0])]
    known = {I550_ROOT: [], VP: route[:1], VP1: route, TANK: [centre(TANK_AT)]}
    searching = page_search._Search(
        client, page_map, VP_RUN, (I550_ROOT, HOME), known, [], set(), 40, 3
    )
    node = page_search._Node(VP1, route, (0, 0), 1)
    assert run(searching._regain(node)) == page_search.ASTRAY
    assert [(p.page, (p.x, p.y), p.landed) for p in panel.presses] == [
        *REGAIN_FROM[start], (I550_ROOT, centre(VP_AT), to),
    ]


# ------------------------------------------------------------ what it presses

STARTS = [HOME, SETTINGS, QUICK, I550_ROOT, VP, VP1, TANK, I550_HISTORY]


@pytest.mark.parametrize("vp_drawn", [True, False])
@pytest.mark.parametrize("start", STARTS)
def test_nothing_under_the_heading_and_never_the_home_screens_button(
    page_search, web_api, start, vp_drawn
):
    panel = pt_like(start=start, vp_drawn=vp_drawn)
    search(page_search, web_api, panel)
    high = [(p.page, p.x, p.y) for p in panel.presses if p.y < 45]
    assert all((x, y) == BACK for _page, x, y in high), high
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == [], (
        "snabbmenyn öppnas aldrig"
    )
    # On the home screen the operation data tile and nothing else.
    tile_icon = panel.screens[11][0]
    assert {(p.x, p.y) for p in panel.presses if p.page == HOME} <= {centre(tile_icon)}
    assert all(press.aimed for press in panel.presses)
    assert SETTINGS not in panel.shown[1:] and QUICK not in panel.shown[1:]
    assert HOT_WATER not in panel.shown[1:]
    assert set(panel.shown) <= MENU | {HOME, start}


@pytest.mark.parametrize("start", STARTS)
def test_controls_are_pressed_only_inside_the_operation_data_menu(page_search, web_api, start):
    panel = pt_like(start=start)
    search(page_search, web_api, panel)
    assert {p.page for p in panel.control_presses} <= MENU | {HOME}


# --------------------------------------------------- the menus it stays out of


def test_a_control_captioned_with_a_forbidden_menu_is_never_pressed(page_search, web_api):
    # A text button that says Service, one that says Språk, and a tile whose
    # caption says Definiera under the icon that takes the press, all on the
    # root and all before the way to the heat pump.
    service = Spec(3, 10, 120, 140, 30, text=2001, target=SERVICE)
    language = Spec(3, 170, 120, 140, 30, text=2003, target=LANGUAGE)
    define_icon = Spec(1, 340, 60, 79, 50, target=DEFINE)
    define_caption = Spec(3, 330, 112, 100, 20, text=2002)
    outside = {
        6000: [heading(2001), Spec(1, 40, 100, 200, 40, target=SETTINGS)],
        6100: [heading(2002), Spec(1, 40, 100, 200, 40, target=SETTINGS)],
        6200: [heading(2003), Spec(1, 40, 100, 200, 40, target=SETTINGS)],
    }
    panel = pt_like(
        dead=0,
        extra_root=(service, language, define_icon, define_caption),
        extra_screens=outside,
        extra_pages={SERVICE: [6000], DEFINE: [6100], LANGUAGE: [6200]},
        extra_parents={SERVICE: I550_ROOT, DEFINE: I550_ROOT, LANGUAGE: I550_ROOT},
    )
    found = search(page_search, web_api, panel)
    assert found.found
    pressed = {(p.x, p.y) for p in panel.presses if p.page == I550_ROOT}
    for spec in (service, language, define_icon, define_caption):
        assert centre(spec) not in pressed, spec
    assert not {SERVICE, DEFINE, LANGUAGE} & set(panel.shown)


@pytest.mark.parametrize("vp_drawn", [True, False])
def test_a_page_whose_heading_names_a_forbidden_menu_is_left_at_once(
    page_search, web_api, vp_drawn
):
    # An uncaptioned box on the root leads to a page headed "Definiera
    # system", whose own button leads back to the root as a page of the menu
    # would: the heading alone keeps it from being searched, even when the
    # page asked for is not drawn and the search goes through every page it
    # has found. Its own button is the one press made on it, the first step
    # of the way back.
    box = Spec(1, 10, 60, 100, 40, target=DEFINE)
    panel = pt_like(
        dead=0,
        vp_drawn=vp_drawn,
        extra_root=(box,),
        extra_screens={6100: [heading(2004), *dead_grid(10, top=100)]},
        extra_pages={DEFINE: [6100]},
        extra_parents={DEFINE: I550_ROOT},
    )
    found = search(page_search, web_api, panel)
    assert found.found is vp_drawn
    assert DEFINE in panel.shown, "rutan provades, den saknar bildtext"
    assert [(p.x, p.y) for p in panel.presses if p.page == DEFINE] == [BACK]


@pytest.mark.parametrize("vp_drawn", [True, False])
def test_a_page_whose_way_back_leaves_the_menu_is_not_searched(page_search, web_api, vp_drawn):
    # A box on the root leads to a page that is not in the operation data
    # menu: its own button leads to the home screen. Nothing on it is
    # pressed but that button, even when the search has nothing better to
    # do, and the search goes on from the root by the tile.
    box = Spec(1, 10, 60, 100, 40, target=ELSEWHERE)
    panel = pt_like(
        dead=0,
        vp_drawn=vp_drawn,
        extra_root=(box,),
        extra_screens={7000: [heading(534), *dead_grid(10, top=100)]},
        extra_pages={ELSEWHERE: [7000]},
        extra_parents={ELSEWHERE: HOME},
    )
    found = search(page_search, web_api, panel)
    assert found.found is vp_drawn
    assert [(p.x, p.y) for p in panel.presses if p.page == ELSEWHERE] == [BACK]
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == []
    assert panel.page == HOME


def test_a_control_whose_caption_cannot_be_read_is_not_pressed(page_search, web_api):
    # A text button whose caption the display gives up in neither language:
    # the search cannot tell where it leads by its words, so it is left alone,
    # and the way to the heat pump is found without it.
    unread = Spec(3, 10, 120, 140, 30, text=2005, target=SERVICE)
    panel = pt_like(
        dead=0,
        extra_root=(unread,),
        extra_screens={6000: [heading(2001), Spec(1, 40, 100, 200, 40, target=SETTINGS)]},
        extra_pages={SERVICE: [6000]},
        extra_parents={SERVICE: I550_ROOT},
        silent=frozenset({"/txt/1/2005", "/txt/0/2005"}),
    )
    found = search(page_search, web_api, panel)
    assert found.found
    assert centre(unread) not in {(p.x, p.y) for p in panel.presses if p.page == I550_ROOT}
    assert SERVICE not in panel.shown


def test_a_page_whose_heading_cannot_be_read_is_not_searched(page_search, web_api):
    # The heading of a page a box leads to does not come in either language,
    # so the search cannot vouch for it: nothing on it is pressed but its own
    # button, even when the page asked for is not drawn and there is nothing
    # better to search.
    box = Spec(1, 10, 60, 100, 40, target=DEFINE)
    panel = pt_like(
        dead=0,
        vp_drawn=False,
        extra_root=(box,),
        extra_screens={6100: [heading(2006), *dead_grid(10, top=100)]},
        extra_pages={DEFINE: [6100]},
        extra_parents={DEFINE: I550_ROOT},
        silent=frozenset({"/txt/1/2006", "/txt/0/2006"}),
    )
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.NOT_FOUND
    assert [(p.x, p.y) for p in panel.presses if p.page == DEFINE] == [BACK]


def test_a_page_asked_for_whose_heading_names_a_forbidden_menu_is_not_searched(
    page_search, web_api
):
    panel = pt_like()
    found = search(page_search, web_api, panel, target=SETTINGS)
    assert found.reason == page_search.OUTSIDE
    assert panel.presses == [], "ingenting trycks"


def test_the_home_screen_asked_for_is_outside(page_search, web_api):
    panel = pt_like(start=I550_ROOT)
    found = search(page_search, web_api, panel, target=HOME)
    assert found.reason == page_search.OUTSIDE
    assert panel.page == I550_ROOT
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == []


# ----------------------------------------------------- the budget and the depth


def chain(depth: int, start: int = HOME) -> FakePanel:
    """A root and a line of pages under it, each one box away from the next."""
    first = 50
    screens = {
        12: home_screen(532, first),
        **OUTSIDE_SCREENS,
    }
    pages = {HOME: [12], **OUTSIDE_PAGES}
    parent = {**OUTSIDE_PARENTS}
    for level in range(depth + 1):
        page = first + level
        screen = page * 10
        nxt = page + 1 if level < depth else None
        screens[screen] = [
            heading(1001), header_icon(284, SETTINGS),
            caption(949, 55), reading("%.1f", 0, y=55),
            Spec(1, 300, 120, 120, 60, target=nxt),
        ]
        pages[page] = [screen]
        parent[page] = page - 1 if level else HOME
    return FakePanel(
        pages=pages, screens=screens, texts=PANEL_TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=start,
    )


def test_a_page_three_presses_from_the_root_is_found(page_search, web_api):
    panel = chain(3)
    found = search(page_search, web_api, panel, target=53)
    assert found.found
    assert len(found.page.route) == 3
    assert chain(3).press_at_root(found.page.route, 50) == 53


def test_a_page_four_presses_from_the_root_is_beyond_the_depth(page_search, web_api):
    panel = chain(4)
    found = search(page_search, web_api, panel, target=54)
    assert found.reason == page_search.NOT_FOUND
    # The page three presses down is reached and stepped back from, and
    # nothing on it is pressed: its box would lead a fourth press down.
    assert 53 in panel.shown and 54 not in panel.shown
    assert [(p.x, p.y) for p in panel.presses if p.page == 53] == [BACK]
    assert found.tries < page_search.MAX_TAPS, "djupet tog slut, inte budgeten"


def test_a_smaller_budget_is_kept_to_the_press(page_search, web_api):
    panel = pt_like()
    found = search(page_search, web_api, panel, max_taps=10)
    assert found.reason == page_search.NOT_FOUND
    assert found.tries == 10 and len(exploring(panel)) == 10
    assert panel.page == HOME


# ------------------------------------------------------- when it cannot go on


def test_a_page_the_panel_cannot_leave_ends_the_search(page_search, web_api):
    # A box on the root opens a dialog whose own button does nothing: the
    # search stops there rather than press on from a page nobody chose.
    box = Spec(1, 10, 60, 100, 40, target=DIALOG)
    panel = pt_like(
        dead=0,
        extra_root=(box,),
        extra_screens={770: [heading(1301), *dead_grid(10, top=60)]},
        extra_pages={DIALOG: [770]},
    )
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.LOST
    assert found.page is None
    assert [p for p in panel.control_presses if p.page == DIALOG] == []
    assert sum(1 for p in panel.presses if p.page == DIALOG) <= 2


def test_a_panel_moved_by_hand_before_the_page_is_read_offers_nothing(page_search, web_api):
    # Somebody at the panel presses it right after the search arrived: what
    # would be read is another page's, so nothing is read and nothing offered.
    panel = pt_like()
    client = panel.client(web_api)
    real = client.async_current_page
    arrived: list[int] = []

    async def and_then_a_hand() -> int:
        page = await real()
        if page == VP1 and not arrived:
            arrived.append(page)
            panel.page = VP  # pressed by hand the moment after the search looked
        return page

    client.async_current_page = and_then_a_hand
    found = run(page_search.async_search_page(client, VP1))
    assert arrived, "sökningen kom fram"
    assert found.reason == page_search.LOST and found.page is None


def test_a_display_that_does_not_answer_is_not_pressed(page_search, web_api):
    panel = pt_like(silent=frozenset({"/sm/all"}))
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.SILENT and found.error
    assert panel.presses == []


def test_a_page_not_in_the_screen_map_is_not_searched(page_search, web_api):
    panel = pt_like()
    found = search(page_search, web_api, panel, target=999)
    assert found.reason == page_search.UNKNOWN
    assert panel.presses == []


def test_a_page_without_a_reading_is_found_but_offers_nothing(page_search, web_api):
    panel = pt_like()
    found = search(page_search, web_api, panel, target=VP)
    assert found.reason == page_search.EMPTY and found.page is None
    assert panel.page == HOME


def test_a_page_read_with_a_gap_is_not_offered(page_search, web_api):
    # The caption of Kompressor VP1's first row does not come in the panel's
    # language, twice: the row would be offered under another name.
    panel = pt_like(silent=frozenset({"/txt/1/1807"}))
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.GAP and found.page is None
    assert panel.page == HOME


def test_the_root_asked_for_is_found_at_once(page_search, web_api, const):
    panel = pt_like()
    found = search(page_search, web_api, panel, target=I550_ROOT)
    assert found.found and found.page.route == [] and found.tries == 0
    assert found.page.title == "Driftinfo system"


#: The two ways the tile's caption is not read: the display answers nothing
#: in English, or it does not answer at all, twice, which the client counts
#: as a caption it would not give up (CtcWebClient.text_misses).
UNREAD_TILE = {
    "english_missing": {"english_missing": frozenset({570})},
    "silent": {"silent": frozenset({"/txt/0/570"})},
}


@pytest.mark.parametrize("unread", sorted(UNREAD_TILE))
def test_a_home_screen_that_cannot_be_recognised_gets_no_control_pressed(
    page_search, web_api, unread
):
    # The walk home steps back from the page asked for to the home screen,
    # whose tile does not read just now: it stops there without a press, so
    # the quick menu is never opened, and the search says it found no menu.
    panel = pt_like(start=VP1, **UNREAD_TILE[unread])
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.NO_MENU
    assert panel.control_presses == []
    assert [p for p in panel.presses if p.page == HOME] == [], "snabbmenyn öppnas aldrig"
    assert QUICK not in panel.shown
    assert panel.page == HOME


#: What the walk home presses before it stops on the unread home screen, by
#: where the panel stood: on the home screen nothing, from the root the
#: panel's own button once.
UNREAD_WALKS = {HOME: [], I550_ROOT: [(I550_ROOT, "back")]}


@pytest.mark.parametrize("unread", sorted(UNREAD_TILE))
@pytest.mark.parametrize("root", [None, I550_ROOT])
@pytest.mark.parametrize("start", sorted(UNREAD_WALKS))
def test_from_the_home_screen_or_the_root_an_unread_tile_presses_nothing_on_the_home_screen(
    page_search, web_api, start, root, unread
):
    # A client that does not know the home screen yet: an entry reloaded a
    # moment ago, one that is not loaded, or one without a ticked page, with
    # and without the root from the stored menu. Neither the page the panel
    # starts on, which may itself be the home screen, nor the page one step
    # up from the root gets the button that opens the quick menu.
    panel = pt_like(start=start, **UNREAD_TILE[unread])
    client = panel.client(web_api)
    client.root = root
    found = run(page_search.async_search_page(client, VP1))
    assert found.reason == page_search.NO_MENU
    assert [(p.page, p.took) for p in panel.presses] == UNREAD_WALKS[start]
    assert QUICK not in panel.shown
    assert panel.page == HOME


def test_an_unread_caption_on_the_root_does_not_keep_the_walk_there(page_search, web_api):
    # The root is never the home screen: a running entry's client knows it,
    # so a caption on it that does not answer does not stop the walk home.
    unread = Spec(3, 10, 120, 140, 30, text=2005)
    panel = pt_like(
        start=I550_ROOT, extra_root=(unread,), silent=frozenset({"/txt/1/2005", "/txt/0/2005"})
    )
    client = panel.client(web_api)
    client.root = I550_ROOT
    found = run(page_search.async_search_page(client, VP1))
    assert found.found
    assert panel.presses[0].page == I550_ROOT and panel.presses[0].took == "back"
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == []


def test_the_way_home_from_another_page_is_no_hint(page_search, web_api):
    # The walk home is made from every page the panel stands on, but only the
    # way from the page asked for says where that page lies: from the heat
    # pump page the search goes as it goes from the home screen, and the
    # tank's dead boxes spend the budget.
    panel = far_parent(start=VP)
    found = search(page_search, web_api, panel)
    assert found.reason == page_search.NOT_FOUND
    assert found.tries == page_search.MAX_TAPS
    assert panel.presses[0].page == VP and panel.presses[0].took == "back"


# ------------------------------------------------------------- the panel lock


def test_a_busy_panel_is_said_at_once_and_nothing_is_asked(page_search, catalogue, web_api):
    panel = pt_like()
    client = panel.client(web_api)

    async def busy():
        async with client.panel:
            with pytest.raises(catalogue.PanelBusy):
                await page_search.async_search_page_locked(client, VP1)

    run(busy())
    assert panel.session.calls == []


def test_the_search_holds_the_panel_lock_while_it_walks(page_search, web_api):
    panel = pt_like()
    client = panel.client(web_api)
    held: list[bool] = []
    real = client.async_click

    async def noting(*args, **kwargs):
        held.append(client.panel.locked())
        return await real(*args, **kwargs)

    client.async_click = noting
    found = run(page_search.async_search_page_locked(client, VP1))
    assert found.found
    assert held and all(held)
    assert not client.panel.locked()


def test_the_forbidden_words_match_whole_words_in_either_language(page_search):
    assert page_search.forbidden("Service")
    assert page_search.forbidden("Definiera system")
    assert page_search.forbidden("Språk")
    assert page_search.forbidden("Avancerat")
    assert not page_search.forbidden("Driftinfo Värmepump")
    assert not page_search.forbidden("Serviceintervall"), "bara hela ord"
    assert not page_search.forbidden(None)


def test_a_running_entrys_root_keeps_the_walk_home_off_an_unread_home_screen(
    page_search, web_api
):
    # The tile's caption does not come in English just now, so the home
    # screen is not recognised. A running entry's client knows the root from
    # the stored menu, and one step up from the root is the home screen
    # whether its tile can be read or not: the walk stops there, and the
    # button that opens the quick menu is not pressed.
    panel = pt_like(start=VP1, english_missing=frozenset({570}))
    client = panel.client(web_api)
    client.root = I550_ROOT
    found = run(page_search.async_search_page(client, VP1))
    assert found.reason == page_search.NO_MENU
    assert [(p.page, p.took) for p in panel.presses] == [
        (VP1, "back"), (VP, "back"), (I550_ROOT, "back"),
    ]
    assert QUICK not in panel.shown


# ------------------------------------------------------------------ the texts

TEXT_FILES = ("strings.json", "translations/en.json", "translations/sv.json")
#: Every way a search can end without the page, as page_search names them.
ENDINGS = ("UNKNOWN", "OUTSIDE", "NO_MENU", "NOT_FOUND", "LOST", "EMPTY", "GAP", "SILENT")


def _options(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))["options"]


def _search_errors() -> dict[str, str]:
    """The options flow's error key per ending, read from its source (it needs Home Assistant)."""
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    block = source.split("SEARCH_ERRORS = {", 1)[1].split("}", 1)[0]
    return dict(re.findall(r"page_search\.([A-Z_]+):\s*\"([a-z_]+)\"", block))


@pytest.mark.parametrize("name", TEXT_FILES)
def test_every_ending_of_a_search_has_its_words_on_the_form(name):
    options = _options(name)
    errors = _search_errors()
    assert sorted(errors) == sorted(ENDINGS), "varje utgång har ett felmeddelande"
    assert {*errors.values(), "panel_busy"} <= set(options["error"])
    assert options["abort"]["one_walk"]
    for step in ("find_page", "find_page_found"):
        assert options["step"][step]["title"] and options["step"][step]["description"]
    assert options["step"]["find_page"]["data"]["page"]
    assert options["step"]["init"]["data"]["find_page"]
    assert options["step"]["init"]["data_description"]["find_page"]


def test_the_endings_the_flow_maps_are_the_ones_the_search_has(page_search):
    assert {name: getattr(page_search, name) for name in ENDINGS} == {
        "UNKNOWN": "unknown", "OUTSIDE": "outside", "NO_MENU": "no_menu",
        "NOT_FOUND": "not_found", "LOST": "lost", "EMPTY": "empty", "GAP": "gap",
        "SILENT": "silent",
    }


@pytest.mark.parametrize("name", TEXT_FILES)
def test_the_forms_ask_only_for_the_placeholders_the_flow_hands_in(name):
    steps = _options(name)["step"]
    handed = {
        "find_page": {"taps", "depth", "screen_map"},
        "find_page_found": {"page", "title", "count", "steps", "tries"},
    }
    for step, keys in handed.items():
        assert set(re.findall(r"\{(\w+)\}", steps[step]["description"])) == keys, step


def test_the_form_says_the_panel_moves_and_which_menus_it_stays_out_of():
    english = _options("strings.json")["step"]["find_page"]["description"]
    swedish = _options("translations/sv.json")["step"]["find_page"]["description"]
    assert "somebody should stand at the panel" in english
    assert "någon bör stå vid panelen" in swedish
    for words in ("Service, Define or the language selection", "never that button on the home"):
        assert words in english, words
    for words in ("Service, Definiera eller språkvalet", "aldrig den knappen på hemskärmen"):
        assert words in swedish, words


def test_the_swedish_search_texts_use_no_dash_as_punctuation():
    options = _options("translations/sv.json")
    texts = [
        options["step"]["init"]["data"]["find_page"],
        options["step"]["init"]["data_description"]["find_page"],
        options["step"]["find_page"]["title"],
        options["step"]["find_page"]["description"],
        options["step"]["find_page"]["data"]["page"],
        options["step"]["find_page"]["data_description"]["page"],
        options["step"]["find_page_found"]["title"],
        options["step"]["find_page_found"]["description"],
        *options["error"].values(),
        options["abort"]["one_walk"],
    ]
    for text in texts:
        assert "–" not in text and "—" not in text and " - " not in text, text
