"""A page a whole reading of the menu did not reach stays in the menu, ticked as it was.

PT's i550 Pro draws the tile that leads to Kompressor VP1 on its schematic
operation data root only in some operating states. On 2026-10-13 the unit
charged hot water for hours; the harvest missed the page from 16:14, and the
reading owed after the update to 0.21.0 swept to its end without it. Folding
that reading in as the menu took page 42 out of the menu and the selection for
good, and its 35 rows were left in the registry with nothing to provide them.
These tests replay that reading and hold every reader of the menu to keeping
a stored page it did not reach.
"""

from __future__ import annotations

#: PT's menu as the i550 Pro lays it out, in the order the sweep found it.
PT_PAGES = [23, 53, 58, 42, 24, 27, 30]
PT_TITLES = {
    23: "CTC EcoZenith i550 Pro",
    53: "Värmesystem",
    58: "Styrenhet",
    42: "Kompressor VP1",
    24: "Varmvatten",
    27: "Extern bufferttank",
    30: "Historisk driftinfo",
}
PT_ROUTES = {23: [], 53: [(250, 63)], 58: [(222, 166)], 42: [(74, 182)],
             24: [(41, 249)], 27: [(121, 249)], 30: [(281, 249)]}


def pt_page(const, number, title=None):
    return const.SlowPage(
        page=number,
        title=title or PT_TITLES[number],
        screens=[0, 1, number * 3],
        route=PT_ROUTES.get(number, [(10, 10)]),
    )


def test_a_reading_that_misses_the_compressor_page_keeps_it_ticked_in_its_place(catalogue, const):
    stored = [pt_page(const, n) for n in PT_PAGES]
    reading = [pt_page(const, n) for n in PT_PAGES if n != 42]
    menu, selected = catalogue.merge_menu(stored, PT_PAGES, reading)
    assert [p.page for p in menu] == PT_PAGES, "Kompressor VP1 står kvar på sin plats"
    assert selected == PT_PAGES, "och skördas fortfarande"
    kept = next(p for p in menu if p.page == 42)
    assert kept.route == [(74, 182)], "med rutten den hade"


def test_a_page_switched_off_and_not_reached_is_let_go(catalogue, const):
    # Unticking a page is how the owner lets it go: the next whole reading that
    # does not reach it drops it from the menu.
    stored = [pt_page(const, n) for n in PT_PAGES]
    chosen = [n for n in PT_PAGES if n != 42]
    reading = [pt_page(const, n) for n in PT_PAGES if n != 42]
    menu, selected = catalogue.merge_menu(stored, chosen, reading)
    assert 42 not in [p.page for p in menu]
    assert selected == chosen


def test_a_page_the_display_no_longer_has_is_let_go(catalogue, const):
    # New software in the display that renumbers its pages: the screen map no
    # longer has the number, which costs no tap to find out.
    stored = [pt_page(const, n) for n in PT_PAGES]
    reading = [pt_page(const, n) for n in PT_PAGES if n != 42]
    in_the_map = frozenset(n for n in PT_PAGES if n != 42)
    menu, selected = catalogue.merge_menu(
        stored, PT_PAGES, reading, root=23, screen_pages=in_the_map
    )
    assert 42 not in [p.page for p in menu]
    assert 42 not in selected
    still_there = frozenset(PT_PAGES)
    menu, _ = catalogue.merge_menu(stored, PT_PAGES, reading, root=23, screen_pages=still_there)
    assert 42 in [p.page for p in menu], "så länge kartan har sidan står den kvar"


def test_the_page_the_set_up_fell_back_on_is_let_go_and_the_root_is_kept(catalogue, const):
    # Where the set-up could not find the menu it offered the page the panel
    # showed, with no route; that page never belonged to the menu. The root
    # has no route either, and stays.
    fallback = const.SlowPage(page=77, title="Dialog", screens=[770])
    root = pt_page(const, 23)
    stored = [fallback, root, pt_page(const, 53)]
    menu, selected = catalogue.merge_menu(stored, [77, 23, 53], [pt_page(const, 53)], root=23)
    assert [p.page for p in menu] == [23, 53]
    assert selected == [23, 53]


def test_an_interrupted_reading_keeps_every_stored_page(catalogue, const):
    # What an interrupted reading did not get to is no evidence of anything,
    # ticked or not.
    stored = [pt_page(const, n) for n in PT_PAGES]
    chosen = [p for p in stored if p.page != 42]
    reading = [pt_page(const, n) for n in (23, 53)]
    menu, fresh = catalogue.menu_after_rescan(stored, chosen, reading, False)
    assert [p.page for p in menu] == PT_PAGES
    assert not fresh


def test_a_page_in_the_selection_alone_is_not_lost(catalogue, const):
    # A page standing in the selection but not in the stored menu, from a
    # restore done halfway, is folded like the rest.
    menu_without = [pt_page(const, n) for n in PT_PAGES if n != 42]
    selection = [pt_page(const, n) for n in PT_PAGES]
    reading = [pt_page(const, n) for n in PT_PAGES if n != 42]
    menu, fresh = catalogue.menu_after_rescan(menu_without, selection, reading, True, root=23)
    assert 42 in [p.page for p in menu]
    assert fresh


def test_the_reading_brings_its_definitions_and_new_pages_come_last_ticked(catalogue, const):
    stored = [pt_page(const, n) for n in PT_PAGES]
    reading = [pt_page(const, n, "Ny rubrik" if n == 53 else None) for n in PT_PAGES if n != 42]
    reading.append(pt_page(const, 61, "Solfångare"))
    menu, selected = catalogue.merge_menu(stored, PT_PAGES, reading)
    assert [p.page for p in menu] == [*PT_PAGES, 61]
    renamed = next(p for p in menu if p.page == 53)
    assert renamed.title == "Ny rubrik", "läsningens definition gäller"
    assert selected == [*PT_PAGES, 61], "en sida menyn inte haft förut kryssas i"


def test_the_options_reading_keeps_an_unreached_page_and_is_still_fresh(catalogue, const):
    stored = [pt_page(const, n) for n in PT_PAGES]
    reading = [pt_page(const, n) for n in PT_PAGES if n != 42]
    menu, fresh = catalogue.menu_after_rescan(stored, stored, reading, True)
    assert [p.page for p in menu] == PT_PAGES, "formuläret erbjuder fortfarande Kompressor VP1"
    assert fresh, "en hel läsning stämplar versionen även när en sida inte nåddes"


def test_an_installation_with_only_its_harvested_pages_keeps_an_unreached_one(catalogue, const):
    # From before the whole menu was kept: the selection is all there is, and
    # the folding is handed it as the stored menu.
    selection = [pt_page(const, n) for n in (23, 42)]
    menu, selected = catalogue.merge_menu(selection, [23, 42], [pt_page(const, 23)])
    assert [p.page for p in menu] == [23, 42]
    assert selected == [23, 42]


def test_a_route_whose_first_tap_moves_nothing_is_given_up_after_one_tap(web_api):
    # The tile to the page is not drawn just now: the first tap leaves the
    # panel on the root. The walk ends there instead of pressing the route's
    # next step on whatever the root has in that place.
    from test_harvest_route import ROOT, Panel, run

    panel = Panel(web_api)
    client = panel.client
    assert run(client.async_goto_page(21, [(80, 255)]))
    panel.page = ROOT
    before = len(panel.presses)
    hidden = [(5, 5), (100, 200)]
    assert not run(client.async_goto_page(24, hidden))
    assert panel.presses[before:] == [(ROOT, (5, 5))], "ett tryck, sedan ges sidan upp"
    assert panel.page == ROOT
