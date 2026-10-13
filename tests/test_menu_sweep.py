"""The menu sweep over a panel that behaves like one, with the real client (roadmap R61).

catalogue.async_discover_pages moves the physical panel more than anything else
in the integration: it presses every plausible control of the operation data
menu, two levels deep, and it is the first thing that runs at a new
installation, on a model nobody here may have seen. test_menu_root.py holds its
guards with a fake client that walks home
by itself. Here the client is CtcWebClient, unchanged, and the fake is the
display (fake_panel.FakePanel): the home screen is found by its tile's English
caption, the tile is pressed on its icon, the root is learnt from the tile, and
every screen is decoded from a definition the panel serves. Two menus, laid out
the way the two models in the houses lay theirs out:

* an i255, whose operation data pages share a tab strip along the bottom, and
  whose history page is two screens;
* an i550 Pro, whose root is a schematic without a strip, with the heat pump's
  pages one level further down behind a strip of their own.

Each page carries a header icon at the top left that leads out of the menu to
the installer's settings, and the home screen's own button opens the quick
menu: the two places a press must never go. The sweep is held to what it
promises: the pages, their routes and their readings exactly; no press under
the heading but the panel's own button, and that never on the home screen; at
most its budget of presses; every press worked out from the page the panel is
showing; the panel back where it started; and, without a root, no control
pressed at all.

One promise does not hold, and the test that says so is marked xfail: a panel
that started on a page inside the menu is not put back there but left on the
home screen. See test_a_panel_started_inside_the_menu_is_put_back_there.
"""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from conftest import FIXTURES
from fake_panel import (
    BACK,
    DisplaySession,
    FakePanel,
    Spec,
    caption,
    centre,
    encode_screen,
    gzipped,
    header_icon,
    heading,
    reading,
    tabs,
    tile,
)

#: The sweep's budget of presses, catalogue._async_explore's max_taps. The
#: number is part of what the sweep promises a panel somebody may be standing
#: at; raising it is a decision, and this test is where it is made.
BUDGET = 26

HOME, QUICK = 1, 488
#: Outside the operation data menu: the installer's settings and hot water.
SETTINGS, HOT_WATER = 300, 400

TEXTS = {
    532: ("Driftinfo", "Operation data"),  # the i255's tile
    570: ("Driftinfo", "Operation data"),  # the i550 Pro's: the same words, another id
    533: ("Avancerat", "Installer"),
    534: ("Varmvatten", "Hot water"),
    284: ("VV-tank", "DHW tank"),  # what a header icon's slot happens to hold
    1107: ("[E017] Givare solpaneler ut", "[E017] Solar panel sensor out"),
    1001: ("Driftinfo översikt", "Operation data overview"),
    308: ("Driftinfo Värmepump", "Operation data heat pump"),
    1002: ("Driftinfo Värme", "Operation data heating"),
    312: ("Driftinfo, Historik", "Stored operation data"),
    949: ("Utetemperatur °C", "Outdoor temp °C"),
    975: ("Framledning °C", "Primary flow °C"),
    913: ("Kompressor", "Compressor"),
    905: ("Brine in/ut °C", "Brine in/out °C"),
    1807: ("Avgiven värme (kW)", "Energy output (kW)"),
    1810: ("Tillförd effekt (kW)", "Power input (kW)"),
    1003: ("Rumstemperatur °C", "Room temp °C"),
    1808: ("Avgiven värme totalt (kWh)", "Energy output total (kWh)"),
    1811: ("Tillförd energi totalt (kWh)", "Energy input total (kWh)"),
    980: ("Total drifttid h", "Total operation time h"),
    1700: ("Antal starter", "Number of starts"),
    1101: ("Driftinfo system", "Operation data system"),
    1102: ("Historik", "History"),
    1103: ("Tank", "Tank"),
    1104: ("Tanktemperatur °C", "Tank temp °C"),
    1105: ("Värmepump", "Heat pump"),
    1106: ("Historisk driftinfo", "Stored operation data"),
    1109: ("Drift /24 h:m", "Operation /24 h:m"),
    1110: ("Kompressor VP1", "Compressor HP1"),
    1111: ("Värmepump drift", "Heat pump operation"),
    1201: ("Trängsel", "Crowd"),
    1202: ("Trängsel A", "Crowd A"),
    1203: ("Trängsel B", "Crowd B"),
    1204: ("Trängsel C", "Crowd C"),
    1301: ("Dialog", "Dialog"),
    1302: ("Återställ larm", "Reset alarms"),
}

#: Pages outside the menu, the same on every panel here. The quick menu holds a
#: control nobody wants pressed by accident.
OUTSIDE_SCREENS = {
    3000: [heading(533), caption(1003, 55), reading("%.1f", 0, y=55)],
    4000: [heading(534)],
    4880: [Spec(3, 40, 100, 200, 40, text=1302, target=SETTINGS)],
}
OUTSIDE_PAGES = {SETTINGS: [3000], HOT_WATER: [4000], QUICK: [4880]}
OUTSIDE_PARENTS = {SETTINGS: HOME, HOT_WATER: HOME, QUICK: HOME}


def home_screen(tile_text: int, root: int | None) -> list[Spec]:
    return [*tile(tile_text, 40, root), *tile(533, 200, SETTINGS), *tile(534, 360, HOT_WATER)]


# ------------------------------------------------------------ an i255

I255_ROOT, HEAT_PUMP, HEATING, HISTORY = 20, 21, 22, 30
I255_TABS = tabs([I255_ROOT, HEAT_PUMP, HEATING, HISTORY], width=120)
TAB = [centre(spec) for spec in I255_TABS]


def i255(start: int = HOME, tile_target: int | None = I255_ROOT, **panel) -> FakePanel:
    header = header_icon(284, SETTINGS)
    screens = {
        10: home_screen(532, tile_target),
        200: [
            heading(1001), header,
            caption(949, 55), reading("%.1f", 0, y=55),
            caption(975, -28), reading("%.1f", 1, y=-28),
            *I255_TABS,
        ],
        118: [
            heading(308), header,
            caption(913, 55), reading("%.-1frps", 37, y=55, width=60),
            caption(905, -28), reading("%.1f", 45, y=-28),
            Spec(3, -20000, -28, 10, 28, fmt=" / "),
            reading("%.1f", 46, y=-28, x=-20000, width=30),
            caption(1807, -28, width=235), reading("%.1f", 116, y=-28, x=240, width=84),
            caption(1810, -28, width=235), reading("%.1f", 122, y=-28, x=240, width=84),
            *I255_TABS,
        ],
        120: [heading(1002), header, caption(1003, 55), reading("%.1f", 3, y=55), *I255_TABS],
        128: [
            heading(312), header,
            caption(1808, 55, width=245), reading("%d", 22, y=55, x=250, width=100),
            caption(1811, -28, width=245), reading("%d", 53, y=-28, x=250, width=100),
            *I255_TABS,
        ],
        129: [
            caption(980, -28, width=245), reading("%d", 10, y=-28, x=250, width=100),
            caption(1700, -28, width=245), reading("%u", 32, y=-28, x=250, width=100),
        ],
        # A dialog: its own button does nothing, and the big button on it leads
        # into the settings. Only the test that starts on it ever sees it.
        770: [
            heading(1301), caption(949, 55), reading("%.1f", 0, y=55),
            Spec(3, 40, 150, 400, 60, text=1302, target=SETTINGS),
        ],
        **OUTSIDE_SCREENS,
    }
    pages = {
        HOME: [10], I255_ROOT: [200], HEAT_PUMP: [118], HEATING: [120], HISTORY: [128, 129],
        77: [770], **OUTSIDE_PAGES,
    }
    parent = {
        I255_ROOT: HOME, HEAT_PUMP: I255_ROOT, HEATING: I255_ROOT, HISTORY: I255_ROOT,
        **OUTSIDE_PARENTS,
    }
    return FakePanel(
        pages=pages, screens=screens, texts=TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=start, **panel,
    )


I255_MENU = {I255_ROOT, HEAT_PUMP, HEATING, HISTORY}


def value(const, page, screen, var, label, fmt, unit, scale, *more):
    return const.SlowValue(
        key=f"p{page}_s{screen}_v{var}", label=label, page=page, screen=screen,
        fmt=fmt, var_indices=[var, *more], unit=unit, scale=scale,
    )


def i255_expected(const) -> list:
    """The i255's menu as the sweep must read it: page, title, screens, route, values."""
    return [
        (I255_ROOT, "Driftinfo översikt", [200], [], [
            value(const, 20, 200, 0, "Utetemperatur", "%.1f", "°C", 0.1),
            value(const, 20, 200, 1, "Framledning", "%.1f", "°C", 0.1),
        ]),
        (HEAT_PUMP, "Driftinfo Värmepump", [118], [TAB[1]], [
            value(const, 21, 118, 37, "Kompressor", "%.-1frps", "rps", 0.1),
            value(const, 21, 118, 45, "Brine in/ut 1", "%.1f", "°C", 0.1),
            value(const, 21, 118, 46, "Brine in/ut 2", "%.1f", "°C", 0.1),
            value(const, 21, 118, 116, "Avgiven värme", "%.1f", "kW", 0.1),
            value(const, 21, 118, 122, "Tillförd effekt", "%.1f", "kW", 0.1),
        ]),
        (HEATING, "Driftinfo Värme", [120], [TAB[2]], [
            value(const, 22, 120, 3, "Rumstemperatur", "%.1f", "°C", 0.1),
        ]),
        (HISTORY, "Driftinfo, Historik", [128, 129], [TAB[3]], [
            value(const, 30, 128, 22, "Avgiven värme totalt", "%d", "kWh", 1.0),
            value(const, 30, 128, 53, "Tillförd energi totalt", "%d", "kWh", 1.0),
            value(const, 30, 129, 10, "Total drifttid", "%d", "h", 1.0),
            value(const, 30, 129, 32, "Antal starter", "%u", None, 1.0),
        ]),
    ]


# ------------------------------------------------------- an i550 Pro

I550_ROOT, VP, TANK, I550_HISTORY, VP1, VP_RUN = 40, 41, 43, 44, 42, 45
VP_TABS = tabs([VP1, VP_RUN, VP], width=150)
TANK_BOX = Spec(1, 40, 60, 120, 100, target=TANK)
VP_BOX = Spec(1, 300, 60, 120, 100, target=VP)
HISTORY_BUTTON = Spec(3, 20, 232, 140, 30, text=1102, target=I550_HISTORY)
#: The picture the schematic is drawn on, under the heading and past the
#: panel's bottom edge: the page itself, not a control.
SCHEMATIC = Spec(1, 0, 45, 480, 250)
#: The figure on the schematic, which no caption names.
FIGURE = reading("%.1f", 0, y=100, x=180, width=60)
OUTDOOR = [caption(949, 180), reading("%.1f", 1, y=180)]


def i550(start: int = HOME, **panel) -> FakePanel:
    header = header_icon(284, SETTINGS)
    screens = {
        11: home_screen(570, I550_ROOT),
        # The root is a schematic: a picture under everything, the tank and the
        # heat pump on it lead to their pages, a figure stands on it with no
        # caption, and the history is a button. No strip along the bottom.
        400: [
            SCHEMATIC, heading(1101), header,
            TANK_BOX, VP_BOX, FIGURE, *OUTDOOR, HISTORY_BUTTON,
        ],
        410: [heading(1105), header, *VP_TABS],
        138: [
            heading(1110), header,
            caption(1807, 55, width=235), reading("%.1f", 118, y=55, x=240, width=84),
            caption(1810, 83, width=235), reading("%.1f", 124, y=83, x=240, width=84),
            *VP_TABS,
        ],
        139: [
            heading(1111), header,
            caption(913, 55), reading("%.-1frps", 37, y=55, width=60),
            *VP_TABS,
        ],
        430: [heading(1103), header, caption(1104, 55), reading("%.1f", 2, y=55)],
        # The history page's header icon shows the alarm, as an i550 Pro's did.
        136: [
            header_icon(1107, SETTINGS), heading(1106),
            caption(1808, 55, width=245), reading("%d", 6, y=55, x=250, width=100),
            caption(1109, 83, width=245), reading("%02d:%02d", 7, 8, y=83, x=250, width=100),
            caption(1811, -28, width=245), reading("%d", 38, y=-28, x=250, width=100),
        ],
        **OUTSIDE_SCREENS,
    }
    pages = {
        HOME: [11], I550_ROOT: [400], VP: [410], VP1: [138], VP_RUN: [139], TANK: [430],
        I550_HISTORY: [136], **OUTSIDE_PAGES,
    }
    parent = {
        I550_ROOT: HOME, VP: I550_ROOT, TANK: I550_ROOT, I550_HISTORY: I550_ROOT,
        VP1: VP, VP_RUN: VP, **OUTSIDE_PARENTS,
    }
    return FakePanel(
        pages=pages, screens=screens, texts=TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=start, **panel,
    )


I550_MENU = {I550_ROOT, VP, TANK, I550_HISTORY, VP1, VP_RUN}


def i550_expected(const) -> list:
    return [
        (I550_ROOT, "Driftinfo system", [400], [], [
            # The figure on the schematic: no caption names it, so it is
            # named after its own index.
            value(const, 40, 400, 0, "Värde 5", "%.1f", None, 0.1),
            value(const, 40, 400, 1, "Utetemperatur", "%.1f", "°C", 0.1),
        ]),
        (TANK, "Tank", [430], [centre(TANK_BOX)], [
            value(const, 43, 430, 2, "Tanktemperatur", "%.1f", "°C", 0.1),
        ]),
        (I550_HISTORY, "Historisk driftinfo", [136], [centre(HISTORY_BUTTON)], [
            value(const, 44, 136, 6, "Avgiven värme totalt", "%d", "kWh", 1.0),
            value(const, 44, 136, 7, "Drift /24", "%02d:%02d", "min", 1.0, 8),
            value(const, 44, 136, 38, "Tillförd energi totalt", "%d", "kWh", 1.0),
        ]),
        # The heat pump's own pages are a level down, behind the strip on the
        # page the heat pump picture leads to; that page has no reading itself.
        (VP1, "Kompressor VP1", [138], [centre(VP_BOX), centre(VP_TABS[0])], [
            value(const, 42, 138, 118, "Avgiven värme", "%.1f", "kW", 0.1),
            value(const, 42, 138, 124, "Tillförd effekt", "%.1f", "kW", 0.1),
        ]),
        (VP_RUN, "Värmepump drift", [139], [centre(VP_BOX), centre(VP_TABS[1])], [
            value(const, 45, 139, 37, "Kompressor", "%.-1frps", "rps", 0.1),
        ]),
    ]


MENUS = {"i255": (i255, I255_ROOT, I255_MENU), "i550": (i550, I550_ROOT, I550_MENU)}


def run(coro):
    return asyncio.run(coro)


def sweep(catalogue, web_api, panel: FakePanel, require_root: bool = False):
    return run(catalogue.async_discover_pages(panel.client(web_api), require_root=require_root))


def described(reading) -> list:
    return [(p.page, p.title, p.screens, p.route, p.values) for p in reading.pages]


# --------------------------------------------- the fake speaks the display's format


def test_the_fake_display_writes_a_definition_the_decoder_reads_as_the_capture(web_api):
    """Screen 118's facit, written back out by the fake and decoded again.

    The facit is what the decoder makes of the captured definition
    (test_widget_golden.py). Written in the fake's shapes and served the way
    the panel serves it, it has to come back exactly: otherwise a sweep test
    below could pass or fail on the fake's mistake rather than the sweep's.
    """
    facit = json.loads((FIXTURES / "golden_wp_118.json").read_text(encoding="utf-8"))["widgets"]
    assert [w["index"] for w in facit] == list(range(len(facit)))
    specs = [
        Spec(
            w["kind"], w["x"], w["y"], w["width"], w["height"], text=w["text_id"],
            fmt=w["value_fmt"], variables=tuple(w["value_vars"] or ()), visible=w["visible"],
        )
        for w in facit
    ]
    source = gzipped(encode_screen(118, specs))

    def answer(method, path, body):
        if path == "/wp/118":
            return 200, source
        if path.startswith("/txt/"):
            return 200, f"T{path.split('/')[3]}".encode()
        return (200, b"|") if path.startswith("/vars/") else (404, b"")

    client = web_api.CtcWebClient(DisplaySession(answer), "192.0.2.10")
    widgets = run(client.async_widgets(118))
    fields = list(facit[0])
    assert [{name: getattr(w, name) for name in fields} for w in widgets] == facit


# ---------------------------------------------------------- what is read


def test_an_i255_menu_is_read_with_every_page_route_and_reading(catalogue, web_api, const):
    panel = i255()
    reading = sweep(catalogue, web_api, panel)
    assert described(reading) == i255_expected(const)
    assert reading.complete and reading.mapped and reading.gaps == []
    assert reading.root == I255_ROOT
    assert reading.outcome()["home_found"] and reading.outcome()["tile_found"]
    assert reading.outcome()["root_entered"]
    # What counts as a control on the root: the strip first, then the row in
    # reading order; the heading, its icon and the rows scrolled away are not.
    root = panel.screens[200]
    assert [(p.x, p.y) for p in panel.control_presses if p.page == I255_ROOT][:6] == [
        *TAB, centre(root[2]), centre(root[3]),
    ]
    assert {(x, y) for page, x, y in exploring(panel) if page == I255_ROOT} == {
        *TAB, centre(root[2]), centre(root[3]),
    }


def test_an_i550_schematic_is_read_two_levels_deep(catalogue, web_api, const):
    panel = i550()
    reading = sweep(catalogue, web_api, panel)
    assert described(reading) == i550_expected(const)
    assert reading.complete and reading.mapped
    assert reading.root == I550_ROOT
    # Without a strip everything is tried in reading order, the picture under
    # it all excepted, and the heading with its icon.
    assert [(p.x, p.y) for p in panel.control_presses if p.page == I550_ROOT][:6] == [
        centre(TANK_BOX), centre(VP_BOX), centre(FIGURE), *map(centre, OUTDOOR),
        centre(HISTORY_BUTTON),
    ]
    assert {(x, y) for page, x, y in exploring(panel) if page == I550_ROOT} == {
        centre(TANK_BOX), centre(VP_BOX), centre(FIGURE), *map(centre, OUTDOOR),
        centre(HISTORY_BUTTON),
    }
    # Two levels and no deeper: the pages found at the second level carry the
    # same strip, and nothing on them is pressed but the way back.
    assert [p for p in panel.control_presses if p.page in (VP1, VP_RUN)] == []


@pytest.mark.parametrize("model", sorted(MENUS))
def test_every_recorded_route_leads_to_its_page_from_the_root(catalogue, web_api, model):
    build, root, _menu = MENUS[model]
    reading = sweep(catalogue, web_api, build())
    for page in reading.pages:
        assert build().press_at_root(page.route, root) == page.page, f"route to page {page.page}"


@pytest.mark.parametrize("model", sorted(MENUS))
def test_the_menu_read_does_not_depend_on_where_the_panel_stood(catalogue, web_api, model):
    build, _root, menu = MENUS[model]
    from_home = described(sweep(catalogue, web_api, build()))
    for start in sorted(menu | {SETTINGS, QUICK}):
        assert described(sweep(catalogue, web_api, build(start=start))) == from_home, start


# ------------------------------------------------------ how it presses

#: Every page the panel may be showing when the sweep starts: the home screen,
#: the settings, the quick menu, and every page of the menu, the root among them.
STARTS = [
    (model, start)
    for model, (_build, _root, menu) in sorted(MENUS.items())
    for start in (HOME, SETTINGS, QUICK, *sorted(menu))
]


@pytest.mark.parametrize(("model", "start"), STARTS)
def test_nothing_under_the_heading_is_pressed_but_the_panels_own_button(
    catalogue, web_api, model, start
):
    # The heading carries the clock, the page's icon and the panel's own
    # button. The icon at the top left leads to the installer's settings here,
    # and on the home screen the panel's own button opens the quick menu.
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    high = [(p.page, p.x, p.y) for p in panel.presses if p.y < 45]
    assert all((x, y) == BACK for _page, x, y in high), high
    assert [p for p in panel.presses if p.page == HOME and (p.x, p.y) == BACK] == [], (
        "snabbmenyn öppnas aldrig när hemskärmen är känd"
    )
    assert SETTINGS not in panel.shown[1:] and HOT_WATER not in panel.shown[1:]
    assert QUICK not in panel.shown[1:]


@pytest.mark.parametrize(("model", "start"), STARTS)
def test_the_panel_never_leaves_the_operation_data_menu(catalogue, web_api, model, start):
    build, _root, menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert set(panel.shown) <= menu | {HOME, start}
    # The only press on the home screen is its tile, which leads into the menu.
    for press in panel.presses:
        if press.page == HOME:
            assert press.landed in menu, press


@pytest.mark.parametrize(("model", "start"), STARTS)
def test_every_press_is_worked_out_from_the_page_the_panel_shows(
    catalogue, web_api, model, start
):
    # The click path names the screens the press was computed from; a press
    # computed for one page and sent while the panel shows another is how a
    # trial script once nearly changed a setting.
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert panel.presses
    assert all(press.aimed for press in panel.presses)


# ------------------------------------------------------------ the budget


def test_the_budget_is_the_one_this_test_holds_the_sweep_to(catalogue):
    signature = inspect.signature(catalogue._async_explore)
    assert signature.parameters["max_taps"].default == BUDGET


def exploring(panel: FakePanel) -> set[tuple[int, int, int]]:
    """The presses that tried a control: every one the first at its place on its page.

    A recorded route replayed to get back to a page presses a place pressed
    before, and the panel's own button and the tile on the home screen are
    the way around, not a try; none of them counts against the budget.
    """
    return {
        (p.page, p.x, p.y) for p in panel.control_presses if p.page != panel.home
    }


@pytest.mark.parametrize("model", sorted(MENUS))
def test_the_two_menus_are_read_within_the_budget(catalogue, web_api, model):
    build, _root, _menu = MENUS[model]
    panel = build()
    sweep(catalogue, web_api, panel)
    assert len(exploring(panel)) <= BUDGET


CROWD_ROOT, CROWD = 50, (51, 52, 53)


def crowded(start: int = HOME) -> FakePanel:
    """A root with a strip of three tabs, each to a page of twelve controls that
    lead nowhere: three plus thirty-six tries, more than the budget allows."""
    grid = [Spec(1, 10 + (k % 4) * 115, 50 + (k // 4) * 45, 100, 35) for k in range(12)]
    row = [caption(949, -28), reading("%.1f", 0, y=-28)]
    screens = {
        12: home_screen(532, CROWD_ROOT),
        500: [heading(1201), *row, *tabs(CROWD, width=160)],
        510: [heading(1202), *row, *grid],
        520: [heading(1203), *row, *grid],
        530: [heading(1204), *row, *grid],
        **OUTSIDE_SCREENS,
    }
    pages = {HOME: [12], CROWD_ROOT: [500], 51: [510], 52: [520], 53: [530], **OUTSIDE_PAGES}
    parent = {CROWD_ROOT: HOME, 51: CROWD_ROOT, 52: CROWD_ROOT, 53: CROWD_ROOT, **OUTSIDE_PARENTS}
    return FakePanel(
        pages=pages, screens=screens, texts=TEXTS, parent=parent,
        home=HOME, quick=QUICK, start=start,
    )


def test_a_crowded_menu_gets_exactly_the_budget_and_no_more(catalogue, web_api):
    panel = crowded()
    reading = sweep(catalogue, web_api, panel)
    tried = exploring(panel)
    assert len(tried) == BUDGET
    # Three tabs, the first page's twelve, then eleven of the second page's;
    # the third page is never swept, only stepped back from.
    assert sum(1 for page, _x, _y in tried if page == 51) == 12
    assert sum(1 for page, _x, _y in tried if page == 52) == BUDGET - 3 - 12
    assert [p for p in panel.control_presses if p.page == 53] == []
    # A budget that runs out is not a page given up: the same menu spends it
    # the same way every time, so the reading still counts as the menu.
    assert reading.complete
    assert [p.page for p in reading.pages] == [CROWD_ROOT, *CROWD]
    assert panel.page == HOME


# ------------------------------------------------------- putting it back


@pytest.mark.parametrize(
    ("model", "start"),
    [(model, start) for model, (_b, root, _m) in sorted(MENUS.items()) for start in (HOME, root)],
)
def test_the_panel_is_put_back_on_the_home_screen_or_the_root(catalogue, web_api, model, start):
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert panel.page == start


@pytest.mark.parametrize("model", sorted(MENUS))
@pytest.mark.parametrize("start", [SETTINGS, QUICK])
def test_a_panel_found_outside_the_menu_is_left_on_the_home_screen(
    catalogue, web_api, model, start
):
    # The way back to the settings or the quick menu would be pressed blind,
    # and those are the menus a press must not wander into: the home screen is
    # where the panel stays (catalogue._async_restore).
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert panel.page == HOME
    assert [p for p in panel.control_presses if p.page == start] == []


INSIDE = [
    (model, start)
    for model, (_b, root, menu) in sorted(MENUS.items())
    for start in sorted(menu - {root})
]


@pytest.mark.parametrize(("model", "start"), INSIDE)
def test_a_panel_started_inside_the_menu_ends_on_that_page_or_at_home(
    catalogue, web_api, model, start
):
    # What holds today, and will hold once the next test passes: never
    # anywhere but where it started or the home screen.
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert panel.page in (start, HOME)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "async_discover_pages promises the panel 'is put back where it started', but "
        "catalogue._async_restore only steps back from where the sweep ended (the root) "
        "and never replays the route the reading recorded to the start page, so a panel "
        "that started on a page inside the operation data menu is left on the home screen"
    ),
)
@pytest.mark.parametrize(("model", "start"), INSIDE)
def test_a_panel_started_inside_the_menu_is_put_back_there(catalogue, web_api, model, start):
    build, _root, _menu = MENUS[model]
    panel = build(start=start)
    sweep(catalogue, web_api, panel)
    assert panel.page == start


# ---------------------------------------------------------- without a root


@pytest.mark.parametrize("require_root", [False, True])
def test_a_home_screen_that_cannot_be_recognised_gets_no_control_pressed(
    catalogue, web_api, require_root
):
    # The tile's caption does not come in English, so no page is home and no
    # tile is found. The search steps back with the panel's own button, on the
    # home screen too, where it opens the quick menu once and closes it again;
    # nothing else is pressed, anywhere.
    panel = i255(start=I255_ROOT, english_missing=frozenset({532}))
    reading = sweep(catalogue, web_api, panel, require_root)
    assert panel.control_presses == []
    assert [(p.page, p.took) for p in panel.presses] == [
        (I255_ROOT, "back"), (HOME, "quick menu"), (QUICK, "back"),
    ]
    assert panel.page == HOME
    assert reading.pages == [] and not reading.complete and reading.root is None
    assert (reading.home_found, reading.tile_found, reading.root_entered) == (False, False, False)


def test_a_page_the_panel_cannot_leave_is_read_as_it_stands(catalogue, web_api, const):
    # A dialog whose own button does nothing: the search stops at the first
    # press that moved nothing, and the page is offered on its own, read but
    # not pressed, and not taken for the menu.
    panel = i255(start=77)
    reading = sweep(catalogue, web_api, panel)
    assert panel.control_presses == []
    assert [(p.page, p.took, p.landed) for p in panel.presses] == [(77, "back", 77)]
    assert described(reading) == [
        (77, "Dialog", [770], [], [value(const, 77, 770, 0, "Utetemperatur", "%.1f", "°C", 0.1)]),
    ]
    assert not reading.complete and not reading.mapped


def test_a_page_the_panel_cannot_leave_gives_a_reader_that_needs_the_root_nothing(
    catalogue, web_api
):
    panel = i255(start=77)
    reading = sweep(catalogue, web_api, panel, require_root=True)
    assert reading.pages == []
    assert panel.control_presses == []


def test_a_tile_that_leads_nowhere_is_pressed_once_and_nothing_else(catalogue, web_api):
    panel = i255(tile_target=None)
    reading = sweep(catalogue, web_api, panel)
    tile_icon = panel.screens[10][0]
    assert [(p.page, p.x, p.y, p.landed) for p in panel.presses] == [
        (HOME, *centre(tile_icon), HOME),
    ]
    assert (reading.home_found, reading.tile_found, reading.root_entered) == (True, True, False)
    assert reading.pages == [] and not reading.mapped
    assert panel.page == HOME


def test_a_display_that_does_not_answer_is_not_pressed_at_all(catalogue, web_api):
    panel = i255(start=HISTORY, silent=frozenset({"/sm/all"}))
    reading = sweep(catalogue, web_api, panel)
    assert panel.presses == []
    assert {path for _method, path, _body in panel.session.calls} == {"/sm/all"}
    assert reading.error and "/sm/all" in reading.error
    assert (reading.home_found, reading.tile_found, reading.root_entered) == (None, None, None)
    assert reading.pages == [] and not reading.mapped
    assert panel.page == HISTORY
