"""How far a walk through the menu got, in the reading, the log and the report (L6).

c71680d6, an i360, has stood with no page read through several releases, and
nobody could say whether its home screen was not recognised, the operation
data tile was not found, the tile led nowhere or the menu was empty: the walk
said the same nothing each time, on debug. Now the reading carries each step,
the walk says it once per run on warning when it found no menu and on info
when it did, and the report carries the count of pages in the stored menu and
two of the steps. One fake panel per level, the real client for the tile, and
the report builder's own output.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from test_menu_root import HOME, FakeMenu

ROOT_PAGE = 20


def run(coro):
    return asyncio.run(coro)


class NoTile(FakeMenu):
    """Home is recognised, but the operation data tile on it cannot be found."""

    tile_found = None

    async def async_goto_operation_root(self):
        if await self.async_goto_home() is None:
            return False
        self.tile_found = False
        return False


class InertTile(FakeMenu):
    """The tile is found, and pressing it leaves the panel where it was."""

    tile_found = None

    async def async_goto_operation_root(self):
        if await self.async_goto_home() is None:
            return False
        self.tile_found = True
        self.taps.append((HOME, "tile"))
        return False


class EmptyMenu(FakeMenu):
    """The root is entered, and no page in it carries a reading."""

    async def async_widgets(self, screen):
        return [w for w in await super().async_widgets(screen) if w.value_fmt is None]


class DiesAfter(FakeMenu):
    """The display stops answering after a number of taps inside the menu."""

    def __init__(self, web_api, *args, taps_left=1, **kwargs):
        super().__init__(web_api, *args, **kwargs)
        self.web_api = web_api
        self.taps_left = taps_left

    async def async_click(self, screens, x, y):
        if (x, y) != (440, 23):
            if self.taps_left <= 0:
                raise self.web_api.CtcWebError("/click/118 timed out")
            self.taps_left -= 1
        return await super().async_click(screens, x, y)


def _menu(web_api, cls=FakeMenu, **kwargs):
    kwargs.setdefault("tabs", {ROOT_PAGE: [21, 22]})
    kwargs.setdefault("root", ROOT_PAGE)
    kwargs.setdefault("start", ROOT_PAGE)
    return cls(web_api, **kwargs)


@pytest.fixture(autouse=True)
def fresh_log(catalogue, monkeypatch):
    """Every test starts a run of its own: nothing said yet."""
    monkeypatch.setattr(catalogue, "_SAID", set())


def _steps(reading):
    return reading.home_found, reading.tile_found, reading.root_entered


# ------------------------------------------------------------- the levels


def test_a_home_screen_that_is_not_recognised(catalogue, web_api):
    reading = run(catalogue.async_discover_pages(_menu(web_api, root=None), require_root=True))
    assert _steps(reading) == (False, False, False)
    assert reading.pages == [] and not reading.mapped
    assert "home screen was not recognised" in reading.how_far()
    assert reading.outcome()["pages"] == 0


def test_a_home_screen_without_the_tile(catalogue, web_api):
    reading = run(catalogue.async_discover_pages(_menu(web_api, NoTile), require_root=True))
    assert _steps(reading) == (True, False, False)
    assert "tile on it was not" in reading.how_far()


def test_a_tile_that_leads_nowhere(catalogue, web_api):
    reading = run(catalogue.async_discover_pages(_menu(web_api, InertTile), require_root=True))
    assert _steps(reading) == (True, True, False)
    assert "did not lead into the menu" in reading.how_far()


def test_an_empty_menu(catalogue, web_api):
    reading = run(catalogue.async_discover_pages(_menu(web_api, EmptyMenu), require_root=True))
    assert _steps(reading) == (True, True, True)
    assert reading.pages == [] and not reading.mapped
    assert "no page with a reading" in reading.how_far()


def test_a_menu_that_is_read(catalogue, web_api):
    reading = run(catalogue.async_discover_pages(_menu(web_api), require_root=True))
    assert _steps(reading) == (True, True, True)
    assert reading.mapped and reading.complete
    assert reading.outcome() == {
        "home_found": True,
        "tile_found": True,
        "root_entered": True,
        "pages": 3,
        "complete": True,
        "gaps": [],
        "error": None,
    }
    assert "3 page(s)" in reading.describe()


def test_without_the_root_the_page_on_show_is_offered_and_the_walk_still_failed(
    catalogue, web_api
):
    # The first set-up reads the page the panel stands on when the root cannot
    # be found; that is a page, not a menu, and the walk says so.
    reading = run(catalogue.async_discover_pages(_menu(web_api, NoTile, start=HOME)))
    assert [page.page for page in reading.pages] == [HOME]
    assert not reading.mapped
    assert "offered on its own" in reading.how_far()


# ------------------------------------------------- the display goes quiet


def test_a_display_that_stops_answering_partway_gives_an_error_and_no_pages(
    catalogue, web_api
):
    panel = _menu(web_api, DiesAfter, taps_left=1)
    reading = run(catalogue.async_discover_pages(panel, require_root=True))
    assert reading.error == "/click/118 timed out"
    assert reading.pages == [] and not reading.complete
    assert reading.root_entered is True, "felet kom inne i menyn"
    assert "inside the operation data menu" in reading.how_far()
    # The panel is put back as after any walk: back to the root it started on.
    assert panel.page == ROOT_PAGE


def test_a_display_that_does_not_answer_at_all(catalogue, web_api):
    class Silent(FakeMenu):
        async def async_screen_map(self, refresh=False):
            raise web_api.CtcWebError("/sm/all timed out")

    panel = _menu(web_api, Silent)
    reading = run(catalogue.async_discover_pages(panel, require_root=True))
    assert reading.error == "/sm/all timed out"
    # No step was tried, so none of them says no: a display that does not
    # answer is not one whose home screen is not recognised (F3.1).
    assert _steps(reading) == (None, None, None)
    assert "before the home screen was found" in reading.how_far()
    assert panel.taps == [], "inget trycktes"


def test_a_display_that_goes_quiet_on_the_way_home_has_tried_no_step(catalogue, web_api):
    class QuietOnBack(FakeMenu):
        async def async_click(self, screens, x, y):
            if (x, y) == (440, 23):
                raise web_api.CtcWebError("/click/440 timed out")
            return await super().async_click(screens, x, y)

    reading = run(catalogue.async_discover_pages(_menu(web_api, QuietOnBack), require_root=True))
    assert reading.error == "/click/440 timed out"
    assert _steps(reading) == (None, None, None)
    assert not reading.mapped


def test_a_display_that_goes_quiet_after_the_home_screen_keeps_that_step(catalogue, web_api):
    class QuietAtTile(FakeMenu):
        async def async_goto_operation_root(self):
            if await self.async_goto_home() is None:
                return False
            raise web_api.CtcWebError("/click/10 timed out")

    reading = run(catalogue.async_discover_pages(_menu(web_api, QuietAtTile), require_root=True))
    assert reading.error == "/click/10 timed out"
    assert _steps(reading) == (True, None, None), "hemskärmen hittades, resten provades aldrig"
    assert "after the home screen was found" in reading.how_far()


def test_a_walk_that_went_its_whole_way_still_says_no(catalogue, web_api):
    # Without the root the page on show is read on its own, and when that
    # read is cut short the search for the root had already answered: its
    # no stands, and the reading is still not taken for a menu.
    class QuietOnThePage(NoTile):
        asked = 0

        async def async_current_page(self):
            # The first answer is where the walk starts; after the search
            # the display has gone quiet.
            self.asked += 1
            if self.asked > 1:
                raise web_api.CtcWebError("/cp timed out")
            return await super().async_current_page()

    reading = run(catalogue.async_discover_pages(_menu(web_api, QuietOnThePage, start=HOME)))
    assert reading.error == "/cp timed out"
    assert _steps(reading) == (True, False, False)
    assert not reading.mapped


# ------------------------------------------------------------- the log


class Addressed(FakeMenu):
    base_url = "http://192.0.2.10:80"


class AddressedNoTile(NoTile):
    base_url = "http://192.0.2.10:80"


def _said(caplog, level):
    return [r.getMessage() for r in caplog.records if r.levelno == level and "menu" in r.getMessage()]


def test_a_failure_is_one_warning_per_run_and_a_success_one_info_line(
    catalogue, web_api, caplog
):
    caplog.set_level(logging.DEBUG, logger="ctc_ecozenith.catalogue")
    for _ in range(3):
        run(catalogue.async_discover_pages(_menu(web_api, AddressedNoTile), require_root=True))
    warnings = _said(caplog, logging.WARNING)
    assert len(warnings) == 1, warnings
    assert "tile on it was not" in warnings[0]

    for _ in range(2):
        run(catalogue.async_discover_pages(_menu(web_api, Addressed), require_root=True))
    infos = [m for m in _said(caplog, logging.INFO) if m.startswith("The display's menu was read")]
    assert len(infos) == 1, infos
    assert len(_said(caplog, logging.WARNING)) == 1, "ingen ny varning"


def test_the_walk_says_whether_it_has_warned_of_no_menu_on_this_display(catalogue, web_api):
    # The background reading asks, so the line that ends its tries is not a
    # second warning of the same thing (F3.3).
    assert not catalogue.warned_of_no_menu(_menu(web_api, AddressedNoTile))
    run(catalogue.async_discover_pages(_menu(web_api, Addressed), require_root=True))
    assert not catalogue.warned_of_no_menu(_menu(web_api, Addressed)), "en läst meny är ingen varning"
    run(catalogue.async_discover_pages(_menu(web_api, AddressedNoTile), require_root=True))
    assert catalogue.warned_of_no_menu(_menu(web_api, AddressedNoTile))
    # Without an address there is no record to go by, and the line that ends
    # the tries stays a warning rather than go unsaid.
    assert not catalogue.warned_of_no_menu(_menu(web_api, NoTile))


def test_two_displays_each_say_their_own(catalogue, web_api, caplog):
    class Other(AddressedNoTile):
        base_url = "http://192.0.2.11:80"

    caplog.set_level(logging.WARNING, logger="ctc_ecozenith.catalogue")
    run(catalogue.async_discover_pages(_menu(web_api, AddressedNoTile), require_root=True))
    run(catalogue.async_discover_pages(_menu(web_api, Other), require_root=True))
    assert len(_said(caplog, logging.WARNING)) == 2


# ------------------------------------------------- the real client's tile


class TileClient:
    """A real CtcWebClient whose panel is simulated below the request layer.

    Page 1 is home and carries the operation data tile while ``tile_readable``
    is true. Pressing the tile leads to page 20, unless ``inert``.
    """

    def __new__(cls, web_api, *, inert=False):
        client = web_api.CtcWebClient(session=None, host="192.0.2.10")
        client.page = 1
        client.tile_readable = True

        async def screen_map(refresh=False):
            return {1: [10], 2: [20], 20: [200]}

        async def current_page():
            return client.page

        async def operation_tile(page):
            if page == 1 and client.tile_readable:
                return 10, [10], (120, 150)
            return None

        async def click(screens, x, y):
            if (x, y) == (120, 150) and client.page == 1:
                if not inert:
                    client.page = 20
            else:
                client.page = {1: 2, 2: 1, 20: 1}[client.page]
            return []

        client.async_screen_map = screen_map
        client.async_current_page = current_page
        client._async_operation_tile = operation_tile
        client.async_click = click
        return client


def test_the_client_says_it_found_the_tile(web_api):
    client = TileClient(web_api)
    assert client.tile_found is None, "inget försök ännu"
    assert run(client.async_goto_operation_root())
    assert client.tile_found is True


def test_the_client_says_when_home_is_known_but_the_tile_cannot_be_read(web_api):
    client = TileClient(web_api)
    assert run(client.async_goto_home()) == 1
    client.tile_readable = False
    assert not run(client.async_goto_operation_root())
    assert client.tile_found is False


def test_the_client_found_the_tile_though_it_led_nowhere(web_api):
    client = TileClient(web_api, inert=True)
    assert not run(client.async_goto_operation_root())
    assert client.tile_found is True


def test_the_client_says_nothing_of_a_tile_when_home_was_not_found(web_api):
    client = TileClient(web_api)
    client.tile_readable = False
    assert not run(client.async_goto_operation_root())
    assert client.tile_found is None


# ------------------------------------------------------------- the report


def _features(stats_extra, **menu):
    return stats_extra.build_extra(
        "EcoZenith i360", has_display=False, control_enabled=True, page_count=0,
        read_failures=0, **menu,
    )["features"]


def test_the_report_tells_an_unread_menu_from_one_with_nothing_ticked(stats_extra):
    unread = _features(stats_extra, menu_pages=0, menu_home=True, menu_root=False)
    assert unread["pages"] == 0 and unread["menu_pages"] == 0
    assert unread["menu_home"] is True and unread["menu_root"] is False
    unticked = _features(stats_extra, menu_pages=6)
    assert unticked["pages"] == 0 and unticked["menu_pages"] == 6
    # No walk in this run: the two steps are left out, not sent as no.
    assert "menu_home" not in unticked and "menu_root" not in unticked


def _reported(stats_extra, catalogue, panel):
    """The report's features after one walk, as the integration hands them over."""
    outcome = run(catalogue.async_discover_pages(panel, require_root=True)).outcome()
    return _features(
        stats_extra, menu_pages=0,
        menu_home=outcome["home_found"], menu_root=outcome["root_entered"],
    )


def test_the_report_does_not_take_a_silent_display_for_a_missing_home_screen(
    stats_extra, catalogue, web_api
):
    class Silent(FakeMenu):
        async def async_screen_map(self, refresh=False):
            raise web_api.CtcWebError("/sm/all timed out")

    silent = _reported(stats_extra, catalogue, _menu(web_api, Silent))
    assert "menu_home" not in silent and "menu_root" not in silent
    unrecognised = _reported(stats_extra, catalogue, _menu(web_api, root=None))
    assert unrecognised["menu_home"] is False and unrecognised["menu_root"] is False
    assert silent != unrecognised, "de två fallen går att skilja i rapporten"


def test_the_report_of_a_display_that_went_quiet_after_home_says_only_that(
    stats_extra, catalogue, web_api
):
    class QuietAtTile(FakeMenu):
        async def async_goto_operation_root(self):
            if await self.async_goto_home() is None:
                return False
            raise web_api.CtcWebError("/click/10 timed out")

    quiet = _reported(stats_extra, catalogue, _menu(web_api, QuietAtTile))
    assert quiet["menu_home"] is True and "menu_root" not in quiet
    no_tile = _reported(stats_extra, catalogue, _menu(web_api, NoTile))
    assert no_tile["menu_home"] is True and no_tile["menu_root"] is False


def test_the_menu_count_is_a_plain_count(stats_extra):
    features = _features(stats_extra, menu_pages=-3, menu_home=1, menu_root=0)
    assert features["menu_pages"] == 0
    assert type(features["menu_pages"]) is int
    assert features["menu_home"] is True and features["menu_root"] is False
