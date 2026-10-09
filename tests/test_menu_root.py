"""The menu sweep stays in the operation data subtree.

The sweep presses every plausible control on a page, over twenty taps under
the heading, which is fine inside Driftinfo, the one subtree that is read only
on every CTC model. It was not fine when the root could not be found and the
page the panel happened to show became the root: a panel left in Avancerat or
on a settings page would have been swept the same way, and a trial script once
nearly changed a setting on exactly such a page. These tests hold the sweep to
starting only from a verified root, stopping when the root is lost, and never
replacing a stored menu with an empty reading.

The fake counts every press, the root search's included. The search steps
back with the panel's own button towards the home screen, and when the home
screen cannot be recognised it presses that button there too, which on an
i550 Pro opens the quick menu; so "nothing is pressed" was never true without
the root, and the promise is the narrower one these tests make: no control
under the heading, only the chrome.
"""

from __future__ import annotations

import asyncio
import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

TAB_WIDTH = 160
TAB_Y = 240
HOME = 1
#: Where the home screen's top right button leads: the quick menu on an i550
#: Pro, whose own back button returns home. The fakes model that round trip.
QUICK_MENU = 2


def run(coro):
    return asyncio.run(coro)


class FakeMenu:
    """A display with an operation data root and tab strips, counting every tap.

    ``tabs`` maps a page to the pages its tabs lead to. Every page carries a
    heading, one named reading and the tabs, so the sweep has both a value to
    record and controls to press. ``root`` is where the operation data menu is,
    or None for a panel on which its tile cannot be read, so that the home
    screen is never recognised either. A page in ``lost`` has a back button
    that does nothing and no way home, like a dialog. ``parents`` adds where
    the back button leads beyond what the tabs imply.

    Home is page 1. Going home works like the real client: back, hop by hop,
    until the page carrying the operation data tile shows, each hop a press
    of the chrome button that is written down. Entering the root is a press
    on that tile, written down as "tile".
    """

    def __init__(self, web_api, tabs, root, start, lost=(), parents=None):
        self.web_api = web_api
        self.tabs = tabs
        self.root = root
        self.page = start
        self.parent = {target: page for page, targets in tabs.items() for target in targets}
        self.parent.update(parents or {})
        if root is not None:
            self.parent.setdefault(root, HOME)   # back from the root lands on the home screen
        self.lost = set(lost)
        self.taps: list[tuple[int, str]] = []

    # ---- the bits of CtcWebClient the sweep uses
    async def async_screen_map(self, refresh=False):
        pages = set(self.tabs) | set(self.parent) | set(self.parent.values()) | {self.page, HOME}
        if self.root is not None:
            pages.add(self.root)
        return {page: [page * 10] for page in pages}

    async def async_current_page(self):
        return self.page

    def _is_home(self):
        # The tile is read through the text catalogue; with no root there is
        # nothing to read, so home looks like any other page.
        return self.page == HOME and self.root is not None

    async def async_goto_home(self, hops=6):
        seen: set[int] = set()
        for _ in range(hops):
            if self._is_home():
                return HOME
            if self.page in seen:
                return None
            seen.add(self.page)
            before = self.page
            await self.async_click([], 440, 23)
            if self.page == before:
                return None
        return HOME if self._is_home() else None

    async def async_goto_operation_root(self):
        if await self.async_goto_home() is None:
            return False
        self.taps.append((HOME, "tile"))
        self.page = self.root
        return True

    async def async_step_back_to(self, target, hops=6):
        for _ in range(hops):
            if self.page == target:
                return True
            before = self.page
            await self.async_click([], 440, 23)
            if self.page == before:
                return False
        return self.page == target

    async def async_widgets(self, screen):
        page = screen // 10
        widget = self.web_api.Widget
        out = [
            widget(index=0, kind=3, x=10, y=5, width=200, height=30, visible=True, label=f"Sida {page}"),
            widget(index=1, kind=3, x=10, y=60, width=120, height=20, visible=True, label="Utetemperatur"),
            widget(index=2, kind=3, x=200, y=60, width=60, height=20, visible=True,
                   value_fmt="%.1f°C", value_vars=[0]),
        ]
        for n, _target in enumerate(self.tabs.get(page, [])):
            out.append(widget(index=10 + n, kind=0, x=n * TAB_WIDTH, y=TAB_Y,
                              width=TAB_WIDTH, height=30, visible=True))
        return out

    async def async_click(self, screens, x, y):
        if (x, y) == (440, 23):
            self.taps.append((self.page, "back"))
            if self.page not in self.lost:
                self.page = self.parent.get(self.page, self.page)
            return []
        for n, target in enumerate(self.tabs.get(self.page, [])):
            if n * TAB_WIDTH <= x < (n + 1) * TAB_WIDTH and TAB_Y <= y < TAB_Y + 30:
                self.taps.append((self.page, f"tab {n}"))
                self.page = target
                return []
        self.taps.append((self.page, f"miss at {x},{y}"))
        return []

    def taps_on(self, page):
        return [what for where, what in self.taps if where == page]

    @property
    def page_controls_pressed(self):
        """Every press that was not the panel's own back button."""
        return [(where, what) for where, what in self.taps if what != "back"]


#: A panel left deep in the settings: Displayinställning under Display under
#: Avancerat under home, whose own button opens the quick menu.
SETTINGS_PARENTS = {386: 40, 40: 30, 30: HOME, HOME: QUICK_MENU, QUICK_MENU: HOME}


# ------------------------------------------------------------- the root


def test_the_subtree_is_swept_from_its_root(catalogue, web_api):
    # The ordinary case, so the tests below are known to be about the guard
    # and not about a sweep that never worked: three tabs, three pages, each
    # with the tap that reached it recorded as its route.
    panel = FakeMenu(web_api, tabs={20: [21, 22, 23]}, root=20, start=HOME)
    reading = run(catalogue.async_discover_pages(panel))
    assert {page.page for page in reading.pages} == {20, 21, 22, 23}
    assert reading.complete
    routes = {page.page: page.route for page in reading.pages}
    assert routes[20] == []
    assert routes[21] == [(TAB_WIDTH // 2, TAB_Y + 15)]
    assert routes[23] == [(2 * TAB_WIDTH + TAB_WIDTH // 2, TAB_Y + 15)]
    assert panel.page == HOME, "panelen ställs tillbaka där den stod"


def test_without_the_root_no_page_control_is_pressed(catalogue, web_api):
    # The panel stands on a settings page full of controls, and the home
    # screen cannot be recognised. The root search steps back with the chrome
    # button, on the home screen too, where it opens and closes the quick
    # menu once; that is the whole of what it presses. The page the panel
    # ends up on is read as it stands: not one control on it, or on any
    # other page, is pressed.
    panel = FakeMenu(web_api, tabs={386: [387, 388, 389]}, root=None, start=386,
                     parents=SETTINGS_PARENTS)
    reading = run(catalogue.async_discover_pages(panel))
    assert panel.page_controls_pressed == []
    assert {what for _where, what in panel.taps} == {"back"}
    assert [page.page for page in reading.pages] == [panel.page]
    assert reading.pages[0].values, "sidan läses som den står"
    assert not reading.complete, "en sida är inte menyn"
    # Without the root there is no known way back to page 386 either, and
    # the panel is not pressed blind: it stays where the search left it.
    assert panel.page == HOME
    assert panel.taps.count((HOME, "back")) == 1, "snabbmenyn öppnas en gång, inte om och om igen"


def test_a_reader_that_needs_the_root_gets_nothing_without_it(catalogue, web_api):
    # Folding one page into a stored menu would replace the menu with that
    # page. The readers that fold ask for the root and take nothing otherwise.
    panel = FakeMenu(web_api, tabs={386: [387, 388, 389]}, root=None, start=386,
                     parents=SETTINGS_PARENTS)
    reading = run(catalogue.async_discover_pages(panel, require_root=True))
    assert reading.pages == []
    assert not reading.complete
    assert panel.page_controls_pressed == []


def test_the_sweep_stops_when_the_root_is_lost(catalogue, web_api):
    # The first tab opens a page the panel cannot leave: back does nothing and
    # home cannot be found. From there the sweep must not press on with the
    # root's remaining targets, nor keep trying the back button for each one.
    panel = FakeMenu(web_api, tabs={20: [21, 22, 23]}, root=20, start=20, lost={21})
    reading = run(catalogue.async_discover_pages(panel))
    assert [what for what in panel.taps_on(20) if what.startswith("tab")] == ["tab 0"]
    assert {page.page for page in reading.pages} == {20, 21}
    assert not reading.complete, "ett avbrutet svep är inte hela menyn"
    # One attempt to get back by the sweep, one by the restore afterwards; the
    # old sweep tried once more for every target it still had, and the search
    # for home is not asked to press the very button that just did nothing.
    assert panel.taps_on(21).count("back") <= 2
    assert not [what for what in panel.taps_on(21) if what.startswith("miss")], (
        "sidan som inte går att lämna sveps inte"
    )


def test_a_lost_route_gives_up_the_page_but_not_the_sweep(catalogue, web_api):
    # The root is regained but the route to a second level page is not: that
    # page is given up and the sweep goes on from the root. A fake whose tab
    # to 22 leads somewhere different the second time it is pressed.
    class Fickle(FakeMenu):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.presses = 0

        async def async_click(self, screens, x, y):
            if self.page == 20 and TAB_WIDTH <= x < 2 * TAB_WIDTH and TAB_Y <= y < TAB_Y + 30:
                self.presses += 1
                if self.presses > 1:
                    self.taps.append((self.page, "tab 1 astray"))
                    self.page = 29          # a page off the map
                    self.parent[29] = 20
                    return []
            return await super().async_click(screens, x, y)

    panel = Fickle(web_api, tabs={20: [21, 22, 23], 22: [24]}, root=20, start=20)
    reading = run(catalogue.async_discover_pages(panel))
    assert {21, 22, 23} <= {page.page for page in reading.pages}
    assert 24 not in {page.page for page in reading.pages}, "sidan bakom den vilsna vägen ges upp"
    assert not reading.complete, "en uppgiven sida gör läsningen ofullständig"
    assert panel.page == 20


# --------------------------------------------------- reading the menu again


def page(const, number):
    return const.SlowPage(page=number, title=f"Sida {number}", screens=[number * 10])


def test_an_empty_rereading_keeps_the_stored_menu(catalogue, const):
    stored = [page(const, 20), page(const, 21), page(const, 22)]
    selected = [page(const, 21)]
    menu, fresh = catalogue.menu_after_rescan(stored, selected, [])
    assert [p.page for p in menu] == [20, 21, 22]
    assert not fresh, "en läsning som inte skedde stämplar ingen version"


def test_a_rereading_that_gave_something_replaces_the_menu(catalogue, const):
    stored = [page(const, 20), page(const, 21)]
    discovered = [page(const, 20), page(const, 21), page(const, 22)]
    menu, fresh = catalogue.menu_after_rescan(stored, [page(const, 20)], discovered)
    assert [p.page for p in menu] == [20, 21, 22]
    assert fresh


def test_an_installation_without_a_stored_menu_falls_back_on_its_pages(catalogue, const):
    # From before the whole menu was kept: only the harvested pages exist.
    menu, fresh = catalogue.menu_after_rescan([], [page(const, 30)], [])
    assert [p.page for p in menu] == [30]
    assert not fresh


def test_both_readers_ask_for_the_root_and_the_rescan_stamps_only_a_fresh_menu():
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "async_discover_pages(client, require_root=True)" in init
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    rescan = flow.split("async def async_step_rescan")[1]
    # The form reads through catalogue.async_rescan_pages, which takes the
    # harvester's panel lock and then asks for the root like the re-read does.
    assert "async_rescan_pages(self._web_client())" in rescan
    helper = (COMPONENT / "catalogue.py").read_text(encoding="utf-8")
    helper = helper.split("async def async_rescan_pages")[1].split("\nasync def ")[0]
    assert "async_discover_pages(client, require_root=True)" in helper
    assert "menu_after_rescan(" in rescan
    assert "if self._fresh:" in rescan, "versionen stämplas bara när menyn kom från panelen"
    # The first set-up keeps offering the page the panel is on: there is no
    # stored menu to lose, and Modbus alone is a good entry either way. But it
    # stamps the version only for the whole menu, so a reading without the
    # root, or one the sweep had to give up on, is read again in the background.
    first = flow.split("async def async_step_slow")[1].split("async def async_step_dhcp")[0]
    assert "async_discover_pages(client)" in first
    before_guard, after_guard = first.split("if self._complete:")
    assert "CONF_MENU_VERSION" not in before_guard, "stämpeln ska ligga bakom villkoret"
    assert "CONF_MENU_VERSION" in after_guard.split("return self.async_create_entry")[0]
