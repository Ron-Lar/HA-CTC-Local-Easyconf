"""The least the display is asked for on a walk (R9).

Every page of a harvest used to begin with a trip home: the client stepped back
to the home screen, read every widget and label of every page it passed to
recognise it, pressed the operation data tile and replayed the route from
there. Hundreds of requests per walk against a server that drops connections
above a handful in flight, and timeouts that Patience answered with walks
every five minutes instead of thirty. Now the operation data tile is
remembered per home page once found, the root's number is known or learnt,
and between two pages the panel steps back to the root and replays the route
from there, the number checked first. The client counts its requests and taps,
and the harvester says per walk what it cost.
"""

from __future__ import annotations

import asyncio
import logging
import pathlib

import pytest

from test_menu_root import HOME as MENU_HOME, FakeMenu

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

HOME = 1
QUICK_MENU = 2
ROOT = 20
OUTSIDE = 30
BACK = (440, 23)
TILE = (380, 100)
#: Routes from the root: three tabs, and a page behind the second tab's own strip.
ROUTES = {21: [(80, 255)], 22: [(240, 255)], 23: [(400, 255)], 24: [(240, 255), (100, 200)]}


def run(coro):
    return asyncio.run(coro)


class Panel:
    """A panel below the request layer of a real CtcWebClient.

    Home carries the operation data tile on its one screen; the tile leads to
    the root; the root's tabs lead to three pages, one of which has a strip of
    its own; the back button climbs the tree, and on the home screen it opens
    the quick menu, whose own button leads home. Page 30 is outside the
    subtree, one step above home. Every press and every reading of a page's
    widgets is written down.
    """

    def __init__(self, web_api) -> None:
        self.client = web_api.CtcWebClient(session=None, host="192.0.2.10")
        self.web_api = web_api
        self.page = HOME
        self.presses: list[tuple[int, tuple[int, int]]] = []
        self.widget_reads: list[int] = []
        self.tile_works = True
        client = self.client

        async def screen_map(refresh=False):
            return {p: [p * 10] for p in (HOME, QUICK_MENU, ROOT, 21, 22, 23, 24, OUTSIDE)}

        async def current_page():
            return self.page

        async def widgets(screen, values=None, globals_=None):
            self.widget_reads.append(screen // 10)
            if screen == HOME * 10:
                icon = web_api.Widget(index=0, kind=1, x=340, y=60, width=80, height=80, visible=True)
                caption = web_api.Widget(index=1, kind=3, x=330, y=145, width=100, height=20,
                                         visible=True, label="Driftinfo", text_id=532)
                return [icon, caption]
            return [web_api.Widget(index=0, kind=3, x=10, y=5, width=200, height=30,
                                   visible=True, label=f"Sida {screen // 10}", text_id=1)]

        async def english(screen, widget, values=None, globals_=None):
            return "Operation data" if widget.text_id == 532 else "Something else"

        async def click(screens, x, y):
            self.presses.append((self.page, (x, y)))
            if (x, y) == BACK:
                self.page = {
                    HOME: QUICK_MENU, QUICK_MENU: HOME, ROOT: HOME, 21: ROOT, 22: ROOT,
                    23: ROOT, 24: 22, OUTSIDE: HOME,
                }[self.page]
                return []
            if self.page == HOME and abs(x - TILE[0]) <= 40 and abs(y - TILE[1]) <= 40:
                if self.tile_works:
                    self.page = ROOT
                return []
            if self.page == ROOT:
                for target, route in ROUTES.items():
                    if route[0] == (x, y) and len(route) == 1:
                        self.page = target
                        return []
                if (x, y) == ROUTES[24][0]:
                    self.page = 22
                return []
            if self.page == 22 and (x, y) == ROUTES[24][1]:
                self.page = 24
            return []

        client.async_screen_map = screen_map
        client.async_current_page = current_page
        client.async_widgets = widgets
        client.async_english_label = english
        client.async_click = click

    def pressed_on(self, page):
        return [point for where, point in self.presses if where == page]

    @property
    def tile_presses(self):
        return [p for p in self.pressed_on(HOME) if p != BACK]


@pytest.fixture()
def panel(web_api):
    return Panel(web_api)


# --------------------------------------------------------- the tile cache


def test_the_operation_data_tile_is_read_once_per_home_page(panel):
    client = panel.client
    assert run(client.async_goto_home()) == HOME
    assert panel.widget_reads == [HOME], "en läsning hittar kaklet"
    assert run(client.async_goto_home()) == HOME
    assert run(client.async_goto_operation_root())
    assert panel.widget_reads == [HOME], "sedan kostar hemskärmen inga avläsningar"
    assert client.root == ROOT, "roten lärs in av trycket på kaklet"


def test_a_tile_whose_press_moves_nothing_is_forgotten(panel):
    client = panel.client
    run(client.async_goto_operation_root())
    panel.page = HOME
    panel.tile_works = False
    assert not run(client.async_goto_operation_root())
    assert client._tiles == {}, "en punkt som inte flyttar panelen glöms"
    panel.tile_works = True
    assert run(client.async_goto_operation_root())
    assert panel.widget_reads == [HOME, HOME], "och letas upp på nytt nästa gång"


# ------------------------------------------------------- between two pages


def test_the_first_page_goes_over_home_and_the_next_steps_back_to_the_root(panel):
    client = panel.client
    assert run(client.async_goto_page(21, ROUTES[21]))
    assert panel.page == 21
    assert panel.tile_presses == [TILE]
    before = len(panel.presses)
    reads = len(panel.widget_reads)

    assert run(client.async_goto_page(22, ROUTES[22]))
    assert panel.page == 22
    assert panel.presses[before:] == [(21, BACK), (ROOT, ROUTES[22][0])], (
        "ett steg tillbaka till roten, sedan rutten"
    )
    assert len(panel.widget_reads) == reads, "hemskärmen läses inte om"
    assert panel.tile_presses == [TILE], "kaklet trycks inte igen"


def test_a_page_two_levels_down_steps_back_twice(panel):
    client = panel.client
    assert run(client.async_goto_page(24, ROUTES[24]))
    assert panel.page == 24
    before = len(panel.presses)
    assert run(client.async_goto_page(21, ROUTES[21]))
    assert panel.presses[before:] == [(24, BACK), (22, BACK), (ROOT, ROUTES[21][0])]


def test_the_root_number_is_checked_before_a_route_is_replayed(panel):
    # Stepping back from a page outside the subtree lands on home, not the
    # root: nothing is replayed from there, nothing is pressed on home but
    # the tile, and the long way is taken.
    client = panel.client
    run(client.async_goto_page(21, ROUTES[21]))
    panel.page = OUTSIDE
    before = len(panel.presses)
    assert run(client.async_goto_page(22, ROUTES[22]))
    assert panel.presses[before:] == [(OUTSIDE, BACK), (HOME, TILE), (ROOT, ROUTES[22][0])]
    assert BACK not in panel.pressed_on(HOME), "hemskärmens knapp trycks aldrig"


def test_the_root_page_itself_is_reached_without_pressing_on_home(panel):
    # The root has no route. Before, stepping back towards it from the home
    # screen pressed the home screen's own button, which opens the quick menu.
    client = panel.client
    client.root = ROOT
    run(client.async_goto_home())
    assert run(client.async_goto_page(ROOT, []))
    assert panel.page == ROOT
    assert BACK not in panel.pressed_on(HOME)
    assert panel.tile_presses == [TILE]
    # And from a page below it, one step back is all.
    panel.page = 23
    before = len(panel.presses)
    assert run(client.async_goto_page(ROOT, []))
    assert panel.presses[before:] == [(23, BACK)]


def test_without_a_known_home_the_short_way_is_not_tried(panel):
    client = panel.client
    client.root = ROOT
    panel.page = 21
    assert not run(client._async_back_to_root()), "utan känd hemskärm ingen genväg"
    assert panel.presses == []


def test_a_root_given_from_the_menu_is_kept(panel):
    client = panel.client
    client.root = ROOT
    run(client.async_goto_operation_root())
    assert client.root == ROOT


# ------------------------------------------------------------- the counts


class FakeResponse:
    def __init__(self, body: bytes = b"17|1|0|") -> None:
        self.body = body
        self.status = 200

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    def __init__(self, stall: int = 0) -> None:
        self.stall = stall

    async def get(self, url, timeout=None):
        if self.stall:
            self.stall -= 1
            raise asyncio.TimeoutError()
        return FakeResponse()

    async def post(self, url, data=None, timeout=None):
        return FakeResponse()


def test_the_client_counts_requests_and_taps(web_api):
    client = web_api.CtcWebClient(FakeSession(), "192.0.2.10")
    run(client.async_vars(118))
    assert (client.requests, client.taps) == (1, 0)
    run(client.async_click([118], 100, 100))
    assert (client.requests, client.taps) == (2, 1)
    # A tap outside the panel presses nothing, and is counted as a request only.
    run(client.async_click_noop([118]))
    assert (client.requests, client.taps) == (3, 1)


def test_a_reading_asked_for_twice_counts_twice(web_api):
    client = web_api.CtcWebClient(FakeSession(stall=1), "192.0.2.10")
    run(client.async_vars(118))
    assert client.requests == 2


def test_the_harvest_says_what_it_cost(caplog, coordinator_module, web_api, const):
    from test_harvest_age import FakeDisplay, _page

    display = FakeDisplay(web_api, {210: [21]})
    display.requests = 0
    display.taps = 0
    web = coordinator_module.CtcWebCoordinator(
        hass=object(), client=display, pages=[_page(const, 21)], interval=1800
    )

    async def goto(target, route=None):
        display.requests += 4
        display.taps += 1
        display.page = target
        return True

    display.async_goto_page = goto
    with caplog.at_level(logging.DEBUG, logger="ctc_ecozenith.coordinator"):
        run(web._async_update_data())
    lines = [r.getMessage() for r in caplog.records if "cost" in r.getMessage()]
    # The page and the way back home: two walks, counted together.
    assert lines == ["Harvest read pages [21] and missed [] at a cost of 8 requests and 2 taps"]


@pytest.fixture(scope="module")
def coordinator_module():
    import ha_stub
    from conftest import load

    ha_stub.skip_unless_stubbed()
    return load("coordinator")


# --------------------------------------------------------------- the root


def test_the_menu_reading_carries_the_root(catalogue, web_api):
    panel = FakeMenu(web_api, tabs={20: [21, 22, 23]}, root=20, start=MENU_HOME)
    reading = run(catalogue.async_discover_pages(panel))
    assert reading.root == 20
    without = FakeMenu(web_api, tabs={386: [387]}, root=None, start=386,
                       parents={386: MENU_HOME, MENU_HOME: 2, 2: MENU_HOME})
    assert run(catalogue.async_discover_pages(without)).root is None


def test_the_root_among_stored_pages_is_the_one_with_no_route(catalogue, const):
    pages = [
        const.SlowPage(page=21, title="A", route=[(80, 255)]),
        const.SlowPage(page=20, title="Driftinfo", route=[]),
    ]
    assert catalogue.menu_root(pages) == 20
    assert catalogue.menu_root(pages[:1]) is None


def test_the_root_is_stored_with_the_menu_and_handed_to_the_client():
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    reread = init.split("async def _async_reread_menu")[1].split("\ndef ")[0]
    assert "CONF_MENU_ROOT" in reread and "reading.root" in reread
    setup = init.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "web_client.root =" in setup
    assert "menu_root(" in setup
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert flow.count("CONF_MENU_ROOT] = self._root") == 2, "båda flödena sparar roten med menyn"
