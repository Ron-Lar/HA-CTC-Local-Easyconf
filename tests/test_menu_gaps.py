"""A page read with a gap is not taken for the page, and no row's entry pays for it (F9.3, F5.1).

MenuReading.complete used to speak for the navigation alone: the root found,
the root regained, every page reached again. It said nothing about what the
pages themselves came back with, and two things can go quietly missing there.
A screen whose widgets would not come is skipped, and its rows with it; a
caption the catalogue would not give up reads as "", and its row is named
"Värde N". On VSH's history page that row is "Avgiven värme totalt (kWh)",
the delivered heat counter: read with that one caption lost, the page carried
p30_varde_11 instead of p30_avgiven_varme_totalt, and a menu written from such
a reading would have had the registry tidy-up remove the counter's entry as a
row the parser no longer builds. Since roadmap L2 the key follows the row's
place, p30_s128_v22 either way, so the entry stays; but the counter is found
by its name, and the coefficient of performance would still lose it.

Now the client counts the captions it would not get, the page read says which
screens it skipped, the sweep leaves a page read with either out of its
reading and calls the reading incomplete, and an incomplete reading is never
written over the stored menu. These hold each step, on the real i255 history
page from the fixtures, and then the chain: a reading in which the history
page's screen timed out leaves the stored page, the counter's entry and the
counter itself in place.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FIXTURES, load
from test_menu_root import FakeMenu

ROOT = 20
HISTORY = 30
TABS = {ROOT: [21, 22, HISTORY]}
#: "Avgiven värme totalt (kWh)" on the i255's history page, screen 128,
#: variable 22: the key follows its place since roadmap L2.
COUNTER = f"p{HISTORY}_s128_v22"
#: The same row in the sweep below, where the fake menu gives page 30 the
#: screen 300.
SWEPT = f"p{HISTORY}_s{HISTORY * 10}_v22"

rows = load("rows")


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def history() -> dict:
    """VSH's history page as the i255 draws it, every caption resolved."""
    return json.loads((FIXTURES / "widgets_i255_128.json").read_text(encoding="utf-8"))


def _caption_index(data: dict) -> int:
    return next(
        w["index"] for w in data["widgets"]
        if (w.get("label") or "").startswith("Avgiven värme totalt")
    )


def _widgets(web_api, data: dict, blank: int | None = None) -> list:
    """The page's widgets, with one caption reading as nothing where asked."""
    out = []
    for w in data["widgets"]:
        fields = dict(w)
        if blank is not None and fields["index"] == blank:
            fields["label"] = ""  # what web_api.async_text answers after two failures
        out.append(web_api.Widget(**fields))
    return out


class PageClient:
    """Hands the page read one real screen; a caption or the screen can fail."""

    def __init__(self, web_api, data: dict, blank: int | None = None, down: bool = False) -> None:
        self._web_api = web_api
        self._data = data
        self._blank = blank
        self._down = down
        self.text_misses = 0

    async def async_widgets(self, screen):
        if self._down:
            raise self._web_api.CtcWebError(f"/wp/{screen} timed out")
        if self._blank is not None:
            self.text_misses += 1  # as the real client counts a swallowed caption failure
        return _widgets(self._web_api, self._data, self._blank)


# ------------------------------------------------------------ one page


def test_a_whole_page_read_says_so_and_carries_the_counter(catalogue, web_api, history):
    reading = run(
        catalogue.async_read_page_values(PageClient(web_api, history), HISTORY, [history["screen"]])
    )
    assert reading.whole and reading.skipped == [] and reading.text_misses == 0
    assert COUNTER in [value.key for value in reading.values]


def test_a_caption_that_did_not_answer_renames_the_row_and_the_reading_says_so(
    catalogue, web_api, history
):
    screen = history["screen"]
    whole = run(catalogue.async_read_page_values(PageClient(web_api, history), HISTORY, [screen]))
    client = PageClient(web_api, history, blank=_caption_index(history))
    reading = run(catalogue.async_read_page_values(client, HISTORY, [screen]))
    keys = [value.key for value in reading.values]
    # The key follows the row's place since L2, so it stays; the name does not.
    assert keys == [value.key for value in whole.values], "nyckeln följer platsen, inte namnet"
    counter = next(value for value in reading.values if value.key == COUNTER)
    assert counter.label.startswith("Värde "), "utan bildtext döps raden Värde N, efter värdets eget index"
    assert len(keys) == len(whole.values), "lika många rader: inget i listan säger att en saknas"
    assert not reading.whole
    assert reading.text_misses == 1 and reading.skipped == []


def test_a_screen_that_did_not_answer_is_skipped_and_the_reading_says_so(
    catalogue, web_api, history
):
    screen = history["screen"]
    reading = run(
        catalogue.async_read_page_values(PageClient(web_api, history, down=True), HISTORY, [screen])
    )
    assert reading.values == []
    assert reading.skipped == [screen]
    assert not reading.whole


def test_the_old_entry_point_still_answers_the_list(catalogue, web_api, history):
    client = PageClient(web_api, history)
    values = run(catalogue.async_page_values(client, HISTORY, [history["screen"]]))
    reading = run(catalogue.async_read_page_values(client, HISTORY, [history["screen"]]))
    assert [v.key for v in values] == [v.key for v in reading.values]


def test_the_client_counts_the_captions_it_would_not_get(web_api):
    from test_text_cache import SCREEN, FakeSession

    # Two stalls on one catalogue entry make one swallowed failure, counted
    # once; an entry that answers is not counted, and is cached as before.
    # The stall is four deep: the failure is not cached, so the next ask for
    # the same entry goes to the display again and may fail again.
    session = FakeSession({f"/txt/1/{308}": 4})
    client = web_api.CtcWebClient(session, "192.0.2.10")
    assert client.text_misses == 0
    assert run(client.async_text(308)) == ""
    assert client.text_misses == 1
    assert run(client.async_text(309)) == "Text 309"
    assert client.text_misses == 1
    # And the whole screen, whose heading is that entry: still read, one more miss.
    widgets = run(client.async_widgets(SCREEN))
    assert widgets
    assert client.text_misses == 2, "bildtexten frågas efter igen och saknas igen"
    # Answered at last: cached, and the count stands where it was.
    assert run(client.async_text(308)) == "Text 308"
    assert run(client.async_text(308)) == "Text 308"
    assert client.text_misses == 2


# ------------------------------------------------------------- the sweep


class GappyMenu(FakeMenu):
    """VSH's root with three tabs, the third leading to the real history page.

    The history page's screen can be down, or one of its captions can read as
    nothing; the other pages are the fake's own, one row each.
    """

    def __init__(self, web_api, data: dict, down: bool = False, blank: int | None = None) -> None:
        super().__init__(web_api, tabs=TABS, root=ROOT, start=ROOT)
        self._data = data
        self._down = down
        self._blank = blank
        self.text_misses = 0

    async def async_widgets(self, screen):
        if screen // 10 != HISTORY:
            return await super().async_widgets(screen)
        if self._down:
            raise self.web_api.CtcWebError(f"/wp/{screen} timed out")
        if self._blank is not None:
            self.text_misses += 1
        return _widgets(self.web_api, self._data, self._blank)


def _sweep(catalogue, web_api, history, **trouble):
    return run(catalogue.async_discover_pages(GappyMenu(web_api, history, **trouble), require_root=True))


def test_a_whole_sweep_has_the_history_page_and_no_gaps(catalogue, web_api, history):
    reading = _sweep(catalogue, web_api, history)
    assert {p.page for p in reading.pages} == {ROOT, 21, 22, HISTORY}
    assert reading.complete and reading.gaps == []
    page = next(p for p in reading.pages if p.page == HISTORY)
    assert SWEPT in [v.key for v in page.values]


def test_a_screen_timeout_leaves_the_page_out_and_the_reading_incomplete(
    catalogue, web_api, history
):
    reading = _sweep(catalogue, web_api, history, down=True)
    assert {p.page for p in reading.pages} == {ROOT, 21, 22}, "sidan med luckan lämnas ute"
    assert reading.gaps == [HISTORY]
    assert not reading.complete, "en läsning med lucka är inte hela menyn"


def test_a_lost_caption_leaves_the_page_out_too(catalogue, web_api, history):
    reading = _sweep(catalogue, web_api, history, blank=_caption_index(history))
    assert {p.page for p in reading.pages} == {ROOT, 21, 22}
    assert reading.gaps == [HISTORY]
    assert not reading.complete


def test_a_reading_without_gaps_is_built_as_before(catalogue):
    reading = catalogue.MenuReading()
    assert reading.gaps == [] and not reading.complete and reading.pages == []


# ------------------------------------------- VSH's heat counter survives


def test_the_heat_counter_survives_a_reading_where_its_screen_timed_out(
    catalogue, web_api, cop, history
):
    """The chain from a bad minute at the display to the registry, HA free.

    The stored menu is a whole reading of the three pages; the registry holds
    an entry per row of it, the delivered heat counter among them. Then the
    menu is read again while the history page's screen times out. The
    reading is incomplete, so the readers keep the stored menu (as
    _async_reread_menu does on not reading.complete, and menu_after_rescan
    here), the tidy-up finds nothing vanished, and the counter is still
    found for the coefficient of performance.
    """
    stored = _sweep(catalogue, web_api, history).pages
    registry = {value.key for page in stored for value in page.values}
    assert SWEPT in registry

    reading = _sweep(catalogue, web_api, history, down=True)
    assert not reading.complete and reading.gaps == [HISTORY]

    menu, fresh = catalogue.menu_after_rescan(stored, stored, reading.pages, reading.complete)
    assert not fresh, "ingen version stämplas"
    assert [p.page for p in menu] == [p.page for p in stored]
    assert next(p for p in menu if p.page == HISTORY) is next(p for p in stored if p.page == HISTORY), (
        "historiksidan är den lagrade, orörd"
    )
    assert rows.vanished_display_keys(menu, registry) == set(), "ingen registerpost pekas ut"
    heat, _consumed = cop.find_energy_totals(menu)
    assert heat is not None and heat.key == SWEPT


def test_what_a_lost_caption_would_have_cost_had_the_page_been_taken_for_read(
    catalogue, web_api, cop, history
):
    # The counterfactual the fix is for: the page as a reading with one lost
    # caption describes it, written over the stored page, loses the counter.
    # Before the keys followed the row's place (L2) it also pointed the
    # tidy-up at the counter's entry; now the entry stays, the key with it,
    # but the counter is found by its name and the name is gone.
    lost = _caption_index(history)
    gappy = run(
        catalogue.async_read_page_values(
            PageClient(web_api, history, blank=lost), HISTORY, [history["screen"]]
        )
    )
    page = load("const").SlowPage(
        page=HISTORY, title="Historik", screens=[history["screen"]], values=gappy.values,
        route=[(400, 255)],
    )
    assert COUNTER not in rows.vanished_display_keys([page], {COUNTER})
    assert cop.find_energy_totals([page])[0] is None
