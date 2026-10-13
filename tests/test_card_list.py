"""All values: empty rows wait behind a box, the count says so, the search is kept (R39).

The i550's root page began the list with ten unnamed rows and the i255 had
"Värde 7" to "Värde 14", always unavailable, and the count did not say how
many were empty. The page now gives the list card a box, "visa även tomma",
off by default: a row whose state is unavailable or unknown, without a word of
the page's own for it, is left out, the count reads shown / with a value /
total, and a search finds what it asks for among the empty rows too. What is
typed and whether the box is ticked are kept in sessionStorage per card, so a
new layout (lovelace_updated builds the card anew) keeps them, and a browser
that refuses sessionStorage still has a list that works.

Worked in node on the stub DOM (tests/card_page.js over tests/card_dom.js).
The search itself is pinned on the card's own _matches.

An unknown row that says why it has no figure, the coefficient of performance
in its first year, has something to show and stays (F3.3). A row without an
explanation has no "i", and its click opens Home Assistant's dialog instead;
only a row whose click does something looks clickable (F3.5).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

WORDS = {"unset": "ej satt", "none_alarm": "inget larm ännu"}
ROWS = [
    {"heading": "Drift"},
    {"entity": "sensor.outdoor", "name": "Utetemperatur", "explanation": "Temperaturen ute.",
     "source": "Modbus-register 62000"},
    {"entity": "sensor.last_alarm", "name": "Senaste larm", "explanation": "Senaste larmet.",
     "source": "Rubrikikonen", "unknown_means": "none_alarm"},
    {"entity": "number.dhw", "name": "Varmvattenbörvärde", "explanation": "Börvärdet.",
     "source": "Modbus-register 1033, flyktigt"},
    {"heading": "CTC EcoZenith i550 Pro"},
    {"entity": "sensor.value_3", "name": "Värde 3", "explanation": "Ett tal utan rubrik.",
     "source": "Displaysidan CTC EcoZenith i550 Pro, skärm 61, rad 1"},
    {"entity": "sensor.value_4", "name": "Värde 4", "explanation": "Ett tal utan rubrik.",
     "source": "Displaysidan CTC EcoZenith i550 Pro, skärm 61, rad 2"},
    {"entity": "sensor.gone", "name": "Värde 5", "explanation": "Ett tal utan rubrik.",
     "source": "Displaysidan CTC EcoZenith i550 Pro, skärm 61, rad 3"},
]
STATES = {
    "sensor.outdoor": {"state": "7.5", "attributes": {"unit_of_measurement": "°C"}},
    "sensor.last_alarm": {"state": "unknown", "attributes": {}},
    "number.dhw": {"state": "unknown", "attributes": {"min": 30, "max": 65}},
    "sensor.value_3": {"state": "unavailable", "attributes": {}},
    "sensor.value_4": {"state": "unknown", "attributes": {}},
    # sensor.gone has no state at all.
}
CONFIG = {
    "rows": ROWS, "states": WORDS, "filter": "Sök bland värdena",
    "empty": "Inget värde matchar sökningen.", "show_empty": "Visa även tomma",
    "count": "visade / med värde / totalt",
}


def _node(script: str, storage: str = "none") -> object:
    """Run `script` with the list's levers, over a sessionStorage of the kind named.

    `storage` is "none" (no sessionStorage at all, as in node), "map" (one that
    works, shared by every card the script mounts) or "refuse" (one whose
    every touch throws, as a locked down browser's may). `mount(config,
    states)` mounts a list card; `shown(card)` is the names on show, in
    order; `count(card)` what the count reads; `search(card, text)` types.
    """
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const CARD = {json.dumps(str(CARD))};
      const kind = {json.dumps(storage)};
      const kept = new Map();
      if (kind === "map") {{
        globalThis.sessionStorage = {{
          getItem: (k) => (kept.has(k) ? kept.get(k) : null),
          setItem: (k, v) => {{ kept.set(k, String(v)); }},
        }};
      }} else if (kind === "refuse") {{
        Object.defineProperty(globalThis, "sessionStorage", {{
          get() {{ throw new Error("SecurityError"); }},
        }});
      }}
      const mount = (config, states) => page.mountCard(CARD, "ctc-ecozenith-rows", config, states);
      const rows = (card) => page.all(card, (e) => e.className === "row");
      const shown = (card) => rows(card).filter((r) => !r.hidden)
        .map((r) => r.children[0].textContent);
      const headings = (card) => page.all(card, (e) => e.className === "heading")
        .map((h) => [h.textContent, !h.hidden]);
      const count = (card) => page.all(card, (e) => e.className === "count")[0].textContent;
      const field = (card) =>
        page.all(card, (e) => e.tagName === "input" && e.type === "search")[0];
      const box = (card) =>
        page.all(card, (e) => e.tagName === "input" && e.type === "checkbox")[0];
      const search = (card, text) => {{ const f = field(card); f.value = text; f.fire("input"); }};
      const tick = (card, on) => {{ const b = box(card); b.checked = on; b.fire("change"); }};
      const CONFIG = {json.dumps(CONFIG)};
      const STATES = {json.dumps(STATES)};
      {script}
    """
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ------------------------------------------------------------- the box


def test_empty_rows_are_left_out_by_default_and_the_count_says_how_many():
    found = _node("""
      const card = mount(CONFIG, STATES);
      console.log(JSON.stringify({shown: shown(card), count: count(card), headings: headings(card),
        box: box(card).checked, title: page.all(card, (e) => e.className === "count")[0].title}));
    """)
    # The outdoor temperature has a value; the last alarm and the hot water
    # setpoint have a word of the page's own for their unknown, which says
    # something. The three display rows have nothing: unavailable, unknown,
    # and no state at all.
    assert found["shown"] == ["Utetemperatur", "Senaste larm", "Varmvattenbörvärde"]
    assert found["count"] == "3 / 3 / 6"
    # A heading with only empty rows under it goes with them.
    assert found["headings"] == [["Drift", True], ["CTC EcoZenith i550 Pro", False]]
    assert found["box"] is False
    assert found["title"] == "visade / med värde / totalt"


def test_ticking_the_box_shows_every_row():
    found = _node("""
      const card = mount(CONFIG, STATES);
      tick(card, true);
      const all = {shown: shown(card), count: count(card)};
      tick(card, false);
      console.log(JSON.stringify({all, again: count(card)}));
    """)
    assert found["all"] == {
        "shown": ["Utetemperatur", "Senaste larm", "Varmvattenbörvärde", "Värde 3", "Värde 4",
                  "Värde 5"],
        "count": "6 / 3 / 6",
    }
    assert found["again"] == "3 / 3 / 6"


def test_a_search_finds_what_it_asks_for_among_the_empty_rows_too():
    found = _node("""
      const card = mount(CONFIG, STATES);
      search(card, "skärm 61");
      const hits = {shown: shown(card), count: count(card)};
      search(card, "rad 2");
      const one = shown(card);
      search(card, "");
      console.log(JSON.stringify({hits, one, cleared: shown(card)}));
    """)
    assert found["hits"] == {"shown": ["Värde 3", "Värde 4", "Värde 5"], "count": "3 / 3 / 6"}
    assert found["one"] == ["Värde 4"]
    assert found["cleared"] == ["Utetemperatur", "Senaste larm", "Varmvattenbörvärde"]


def test_a_row_that_gets_a_value_comes_into_the_list():
    found = _node("""
      const card = mount(CONFIG, STATES);
      card.hass = {states: {...STATES, "sensor.value_3": {state: "41.5", attributes: {}}},
                   callService: () => Promise.resolve()};
      console.log(JSON.stringify({shown: shown(card), count: count(card)}));
    """)
    assert found == {
        "shown": ["Utetemperatur", "Senaste larm", "Varmvattenbörvärde", "Värde 3"],
        "count": "4 / 4 / 6",
    }


def test_a_list_without_the_box_shows_every_row_as_before():
    # The lists on Performance, and a page built by an older integration:
    # without show_empty nothing is left out for being empty, and the count
    # has its two numbers.
    found = _node("""
      const {show_empty, count: words, ...rest} = CONFIG;
      const card = mount(rest, STATES);
      console.log(JSON.stringify({
        shown: shown(card), count: count(card), box: Boolean(box(card))}));
    """)
    assert found["shown"] == [
        "Utetemperatur", "Senaste larm", "Varmvattenbörvärde", "Värde 3", "Värde 4", "Värde 5",
    ]
    assert found["count"] == "6 / 6"
    assert found["box"] is False


def test_an_unknown_row_that_says_why_it_has_no_figure_is_not_empty():
    # F3.3: the coefficient of performance stays available without a figure
    # so that its reason can be read under it. Such a row says something and
    # stays out from behind the box, counted among the rows with something
    # to show, the middle number. Without a reason to show, or unavailable,
    # it is empty as before.
    found = _node("""
      const cop = (entity, name) => ({entity, name, explanation: "Värmefaktorn.",
        source: "Räknad", sub: ["underlag", "basis"], reason: ["skäl", "reason"]});
      const config = {...CONFIG, rows: [...CONFIG.rows, cop("sensor.cop_year", "Värmefaktor år"),
        cop("sensor.cop_day", "Värmefaktor dygn"),
        cop("sensor.cop_life", "Värmefaktor livslängd")]};
      const states = {...STATES,
        "sensor.cop_year": {state: "unknown", attributes: {"skäl": "Inget års underlag ännu"}},
        "sensor.cop_day": {state: "unknown", attributes: {}},
        "sensor.cop_life": {state: "unavailable", attributes: {"skäl": "Räknaren saknas"}},
      };
      const card = mount(config, states);
      const year = rows(card).find((r) => r.children[0].textContent === "Värmefaktor år");
      const sub = year.children.find((c) => c.className === "sub");
      console.log(JSON.stringify({shown: shown(card), count: count(card), sub: sub.textContent}));
    """)
    assert found["shown"] == (
        ["Utetemperatur", "Senaste larm", "Varmvattenbörvärde", "Värmefaktor år"]
    )
    assert found["count"] == "4 / 4 / 9"
    assert found["sub"] == "Inget års underlag ännu"


def test_a_row_without_an_explanation_opens_home_assistants_dialog():
    # F3.5: a list written by hand, or started from the card picker, has rows
    # without an explanation and so without an "i". Their click was passed
    # on to an "i" that was not there and did nothing, under a pointer that
    # said it would. It opens Home Assistant's dialog for the entity now, a
    # row with an "i" still opens its note, and only a row whose click does
    # something looks clickable.
    found = _node("""
      const config = {rows: [
        {entity: "sensor.outdoor", name: "Utetemperatur"},
        {entity: "sensor.flow", name: "Framledning", explanation: "Fram."},
        {name: "Bara text"},
      ]};
      const card = mount(config, {
        "sensor.outdoor": {state: "7.5", attributes: {}},
        "sensor.flow": {state: "35.0", attributes: {}},
      });
      const events = [];
      card.dispatchEvent = (event) => events.push([event.type, event.detail.entityId,
                                                    event.bubbles, event.composed]);
      const [plain, explained, text] = rows(card);
      plain.fire("click");
      explained.fire("click");
      text.fire("click");
      const notes = page.all(card, (e) => e.className === "note").map((n) => !n.hidden);
      const style = page.styleOf(card);
      const rule = style.match(/\\.row \\{([^}]*)\\}/)[1];
      console.log(JSON.stringify({events, notes,
        tap: [plain, explained, text].map((r) => r.dataset.tap || null),
        plainRule: rule.includes("cursor"),
        tapRule: style.includes('.row[data-tap="1"] { cursor: pointer; }'),
        hover: style.includes('.row[data-tap="1"]:hover'), bareHover: /\\.row:hover/.test(style)}));
    """)
    assert found["events"] == [["hass-more-info", "sensor.outdoor", True, True]]
    assert found["notes"] == [False, True, False]
    assert found["tap"] == ["1", "1", None]
    assert found["plainRule"] is False
    assert found["tapRule"] is True
    assert found["hover"] is True
    assert found["bareHover"] is False


# ---------------------------------------------------------- _matches


def test_matches_looks_through_everything_a_row_says_with_every_word_required():
    found = _node("""
      const card = mount(CONFIG, STATES);
      const ask = (query) => {
        card._query = query;
        return card._items.filter((row) => card._matches(row)).map((row) => row.item.name);
      };
      console.log(JSON.stringify({
        name: ask("utetemperatur"),
        explanation: ask("utan rubrik"),
        source: ask("62000"),
        entity: ask("sensor.last_alarm"),
        value: ask("7.5"),
        word: ask("ännu"),
        both: ask("värde gone skärm"),
        neither: ask("utetemperatur skärm"),
        nothing: ask("").length,
      }));
    """)
    assert found == {
        "name": ["Utetemperatur"],
        "explanation": ["Värde 3", "Värde 4", "Värde 5"],
        "source": ["Utetemperatur"],
        "entity": ["Senaste larm"],
        "value": ["Utetemperatur"],
        "word": ["Senaste larm"],
        "both": ["Värde 5"],
        "neither": [],
        "nothing": 6,
    }


def test_the_search_is_matched_without_regard_to_case_or_surrounding_space():
    found = _node("""
      const card = mount(CONFIG, STATES);
      search(card, "  UTETEMPERATUR ");
      console.log(JSON.stringify(shown(card)));
    """)
    assert found == ["Utetemperatur"]


# ---------------------------------------------------- sessionStorage


def test_the_search_and_the_box_come_back_when_the_card_is_built_anew():
    found = _node("""
      const first = mount(CONFIG, STATES);
      search(first, "skärm");
      tick(first, true);
      const key = [...kept.keys()];
      // lovelace_updated: the layout is fetched again and the card made anew.
      const second = mount(CONFIG, STATES);
      const back = {typed: field(second).value, box: box(second).checked, shown: shown(second)};
      // Another pump's list is kept apart.
      const other = mount({...CONFIG, rows: [{entity: "sensor.other", name: "Annan"}]}, {});
      console.log(JSON.stringify({key, stored: JSON.parse(kept.get(key[0])), back,
        other: {typed: field(other).value, box: box(other).checked}}));
    """, storage="map")
    assert found["key"] == ["ctc-ecozenith-rows:sensor.outdoor"]
    assert found["stored"] == {"query": "skärm", "all": True}
    assert found["back"] == {
        "typed": "skärm", "box": True, "shown": ["Värde 3", "Värde 4", "Värde 5"],
    }
    assert found["other"] == {"typed": "", "box": False}


def test_a_key_the_page_gives_names_the_list():
    found = _node("""
      const card = mount({...CONFIG, storage_key: "values-vsh"}, STATES);
      search(card, "x");
      console.log(JSON.stringify([...kept.keys()]));
    """, storage="map")
    assert found == ["ctc-ecozenith-rows:values-vsh"]


def test_what_was_kept_in_another_shape_is_left_alone():
    found = _node("""
      kept.set("ctc-ecozenith-rows:sensor.outdoor", "not json");
      const broken = mount(CONFIG, STATES);
      kept.set("ctc-ecozenith-rows:sensor.outdoor", JSON.stringify({query: 7, all: "yes"}));
      const odd = mount(CONFIG, STATES);
      console.log(JSON.stringify([
        [field(broken).value, box(broken).checked, count(broken)],
        [field(odd).value, box(odd).checked, count(odd)],
      ]));
    """, storage="map")
    assert found == [["", False, "3 / 3 / 6"], ["", False, "3 / 3 / 6"]]


@pytest.mark.parametrize("storage", ["none", "refuse"])
def test_the_list_works_without_sessionstorage(storage):
    found = _node("""
      const card = mount(CONFIG, STATES);
      search(card, "skärm");
      const searched = shown(card);
      tick(card, true);
      search(card, "");
      console.log(JSON.stringify({searched, all: shown(card).length, count: count(card)}));
    """, storage=storage)
    assert found == {
        "searched": ["Värde 3", "Värde 4", "Värde 5"], "all": 6, "count": "6 / 3 / 6",
    }
