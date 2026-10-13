"""One "i", one toggle, real aria attributes (R41), worked in node.

The list's row was a button of its own (role=button, tabIndex 0) with the "i"
inside it, so a screen reader met two toggles for every note and the keyboard
two tab stops per row, 240 on All values. Only the row said aria-expanded; the
"i" on the chips, the key figures and the controls said nothing about the note
it opens. The name's hover text was the explanation once more, read twice. The
chips shared one note while what is open was kept per entity, so two chips in
a row left the wrong text open. The focus ring was 1 px.

Now the "i" is the one toggle on every card, with aria-expanded and
aria-controls naming the note, which gets an id of its own. The row is no
button and no tab stop; a click on it is passed on to the "i". The name has no
hover text. Every chip has its own note, led by its name. The ring is 2 px.
The glyph stays U+24D8, as on the NIBE card.

Since the "i" is the one tab stop per value it is named after its value,
"Förklaring: Utetemperatur" (F3.1), and the controls themselves carry their
value's name, the list, the slider, the field and a button alike (F3.8). A
note left open before a new setConfig on the same card opens with the first
click again (F3.7).
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

STATES = {
    "sensor.outdoor": {"state": "7.5", "attributes": {"unit_of_measurement": "°C"}},
    "sensor.flow": {"state": "35.0", "attributes": {"unit_of_measurement": "°C"}},
    "sensor.status": {"state": "Redo för start", "attributes": {}},
    "sensor.hp_status": {"state": "Till värme", "attributes": {}},
    "number.dhw": {"state": "unknown", "attributes": {"min": 30, "max": 65, "step": 1}},
}
ROWS = [
    {"heading": "Temperaturer"},
    {"entity": "sensor.outdoor", "name": "Utetemperatur", "explanation": "Ute.",
     "source": "Modbus-register 62000"},
    {"entity": "sensor.flow", "name": "Framledning", "explanation": "Fram.",
     "source": "Modbus-register 62004"},
]
CHIPS = [
    {"entity": "sensor.status", "name": "Systemstatus", "explanation": "Hela systemet."},
    {"entity": "sensor.hp_status", "name": "Värmepump status", "explanation": "Värmepumpen."},
]


def _node(script: str) -> object:
    """Run `script` with the page mount as `page`, the card's path as CARD and
    the states above as STATES, and hand back what it prints as JSON."""
    program = (
        f"const page = require({json.dumps(str(PAGE))});\n"
        f"const CARD = {json.dumps(str(CARD))};\n"
        f"const STATES = {json.dumps(STATES)};\n"
        "const by = (card, cls) => page.all(card, (e) => e.className === cls);\n"
        "const aria = (why) => ({controls: why.getAttribute('aria-controls'),"
        " expanded: why.getAttribute('aria-expanded')});\n"
        f"{script}\n"
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_row_is_no_button_and_its_i_is_the_one_toggle():
    found = _node(f"""
      const card = page.mountCard(CARD, "ctc-ecozenith-rows", {{rows: {json.dumps(ROWS)},
        explain: "Förklaring"}}, STATES);
      const rows = by(card, "row");
      const whys = by(card, "why");
      const notes = by(card, "note");
      const before = whys.map(aria);
      whys[0].fire("click");
      const opened = {{aria: aria(whys[0]), hidden: notes[0].hidden}};
      // A click elsewhere on the row is passed on to the "i", which closes it.
      rows[0].fire("click");
      const closed = {{aria: aria(whys[0]), hidden: notes[0].hidden}};
      console.log(JSON.stringify({{
        row: rows.map((r) => ({{role: r.getAttribute("role"),
                               tab: r.tabIndex === undefined ? null : r.tabIndex,
                               expanded: r.getAttribute("aria-expanded")}})),
        ids: notes.map((n) => n.id),
        before, opened, closed,
        glyph: whys[0].textContent.codePointAt(0).toString(16),
        label: whys[0].getAttribute("aria-label"),
      }}));
    """)
    assert found["row"] == [{"role": None, "tab": None, "expanded": None}] * 2
    first, second = found["ids"]
    assert first and second and first != second
    assert found["before"] == [
        {"controls": first, "expanded": "false"}, {"controls": second, "expanded": "false"},
    ]
    assert found["opened"] == {"aria": {"controls": first, "expanded": "true"}, "hidden": False}
    assert found["closed"] == {"aria": {"controls": first, "expanded": "false"}, "hidden": True}
    assert found["glyph"] == "24d8"
    # The "i" is the one tab stop per value, so it says which value it is for.
    assert found["label"] == "Förklaring: Utetemperatur"


@pytest.mark.parametrize("name, field, items", [
    ("ctc-ecozenith-chips", "items", CHIPS),
    ("ctc-ecozenith-readings", "items", ROWS[1:]),
    ("ctc-ecozenith-controls", "items", ROWS[1:]),
    ("ctc-ecozenith-rows", "rows", ROWS),
])
def test_no_name_repeats_its_explanation_as_hover_text_and_every_i_names_its_note(
    name, field, items
):
    found = _node(f"""
      const card = page.mountCard(CARD, {json.dumps(name)},
        {{{json.dumps(field)}: {json.dumps(items)}}}, STATES);
      const notes = new Set(by(card, "note").map((n) => n.id));
      console.log(JSON.stringify({{
        titles: by(card, "label").map((l) => l.title || null),
        named: by(card, "why").map((w) => notes.has(w.getAttribute("aria-controls"))),
        expanded: by(card, "why").map((w) => w.getAttribute("aria-expanded")),
      }}));
    """)
    count = len([item for item in items if "entity" in item])
    assert found["titles"] == [None] * count
    # A control has a second "i" beside its widget, for when it has no value.
    assert len(found["named"]) >= count
    assert set(found["named"]) == {True}
    assert set(found["expanded"]) == {"false"}


def test_every_chip_has_a_note_of_its_own_led_by_its_name():
    found = _node(f"""
      const card = page.mountCard(CARD, "ctc-ecozenith-chips",
        {{items: {json.dumps(CHIPS)}}}, STATES);
      const whys = by(card, "why");
      const notes = by(card, "note");
      whys[0].fire("click");
      whys[1].fire("click");
      const both = notes.map((n) => !n.hidden);
      // Closing the first leaves the second open: each "i" has its own note.
      whys[0].fire("click");
      console.log(JSON.stringify({{
        count: notes.length, both, after: notes.map((n) => !n.hidden),
        aria: whys.map(aria), ids: notes.map((n) => n.id),
        lead: notes[1].children[0].className + "|" + notes[1].children[0].textContent,
      }}));
    """)
    assert found["count"] == 2
    assert found["both"] == [True, True]
    assert found["after"] == [False, True]
    assert [a["controls"] for a in found["aria"]] == found["ids"]
    assert [a["expanded"] for a in found["aria"]] == ["false", "true"]
    assert found["lead"] == "lead|Värmepump status: "


def test_a_chip_that_leaves_the_line_closes_its_note():
    # A boolean chip goes when it turns off; its note goes with it, closed,
    # so the "i" opens it at once when the chip is back.
    found = _node("""
      const items = [{entity: "binary_sensor.run", name: "Kompressor i drift",
                      explanation: "Går.", on_state: "on"}];
      const on = {"binary_sensor.run": {state: "on", attributes: {}}};
      const off = {"binary_sensor.run": {state: "off", attributes: {}}};
      const card = page.mountCard(CARD, "ctc-ecozenith-chips", {items}, on);
      const why = by(card, "why")[0];
      const note = by(card, "note")[0];
      why.fire("click");
      card.hass = {states: off, callService: () => Promise.resolve()};
      const gone = {hidden: note.hidden, expanded: why.getAttribute("aria-expanded")};
      card.hass = {states: on, callService: () => Promise.resolve()};
      why.fire("click");
      console.log(JSON.stringify({gone, back: !note.hidden}));
    """)
    assert found == {"gone": {"hidden": True, "expanded": "false"}, "back": True}


def test_both_is_of_a_control_without_a_value_follow_its_one_note():
    # The hot water setpoint has no value until written: an "i" after its name
    # and one beside the empty slider, both opening the same note.
    found = _node("""
      const items = [{entity: "number.dhw", name: "Varmvattenbörvärde", explanation: "Börvärdet."}];
      const card = page.mountCard(CARD, "ctc-ecozenith-controls", {items}, STATES);
      const whys = by(card, "why");
      const note = by(card, "note")[0];
      whys[1].fire("click");
      console.log(JSON.stringify({id: note.id, open: !note.hidden, aria: whys.map(aria)}));
    """)
    assert found["open"] is True
    assert found["aria"] == [{"controls": found["id"], "expanded": "true"}] * 2


def test_a_row_the_search_hides_closes_its_note_and_opens_with_one_click_when_back():
    found = _node(f"""
      const card = page.mountCard(CARD, "ctc-ecozenith-rows", {{rows: {json.dumps(ROWS)},
        filter: "Sök"}}, STATES);
      const search = page.all(card, (e) => e.tagName === "input" && e.type === "search")[0];
      const why = by(card, "why")[0];
      const note = by(card, "note")[0];
      why.fire("click");
      search.value = "framledning"; search.fire("input");
      const hidden = {{note: note.hidden, expanded: why.getAttribute("aria-expanded")}};
      search.value = ""; search.fire("input");
      why.fire("click");
      console.log(JSON.stringify({{hidden, reopened: !note.hidden}}));
    """)
    assert found == {"hidden": {"note": True, "expanded": "false"}, "reopened": True}


@pytest.mark.parametrize("name, field, items", [
    ("ctc-ecozenith-chips", "items", CHIPS),
    ("ctc-ecozenith-readings", "items", ROWS[1:]),
    ("ctc-ecozenith-controls", "items", [
        *ROWS[1:],
        {"entity": "number.dhw", "name": "Varmvattenbörvärde", "explanation": "Börvärdet."},
    ]),
    ("ctc-ecozenith-rows", "rows", ROWS),
])
def test_every_i_is_named_after_its_value(name, field, items):
    # F3.1: since the row is no button the "i" is the only tab stop per value,
    # and 120 of them all called "Förklaring" did not say which was which.
    # The second "i" of a control without a value is named the same way.
    found = _node(f"""
      const card = page.mountCard(CARD, {json.dumps(name)},
        {{{json.dumps(field)}: {json.dumps(items)}, explain: "Förklaring"}}, STATES);
      const plain = page.mountCard(CARD, {json.dumps(name)},
        {{{json.dumps(field)}: {json.dumps(items)}}}, STATES);
      console.log(JSON.stringify({{
        labels: by(card, "why").map((w) => w.getAttribute("aria-label")),
        plain: by(plain, "why").map((w) => w.getAttribute("aria-label")),
      }}));
    """)
    names = [item["name"] for item in items if "entity" in item]
    # A control also has an "i" beside its widget, for when it has no value,
    # named the same as the one after its name.
    assert len(found["labels"]) >= len(names)
    assert set(found["labels"]) == {f"Förklaring: {n}" for n in names}
    # Without the page's word for it the "i" is an Explanation, still named.
    assert set(found["plain"]) == {f"Explanation: {n}" for n in names}


def test_every_control_is_named_after_its_value():
    # F3.8: the list, the slider, the field and a button said only their
    # value and their role, "Normal, popup button", "50, slider".
    found = _node("""
      const states = {
        "select.dhw_mode": {state: "Normal", attributes: {options: ["Släpp styrningen", "Normal"]}},
        "number.max_rps": {state: "50", attributes: {min: 0, max: 120, step: 1}},
        "number.room": {state: "21.5", attributes: {min: 10, max: 30, step: 0.1}},
        "button.release_all": {state: "unknown", attributes: {}},
      };
      const items = [
        {entity: "select.dhw_mode", name: "Varmvattenprogram"},
        {entity: "number.max_rps", name: "Max varvtal"},
        {entity: "number.room", name: "Rumsbörvärde"},
        {entity: "button.release_all", name: "Släpp all styrning"},
        {entity: "number.unnamed", widget: "field"},
      ];
      states["number.unnamed"] = {state: "5", attributes: {min: 0, max: 10, step: 1}};
      const card = page.mountCard(CARD, "ctc-ecozenith-controls", {items, press: "Tryck"}, states);
      const label = (test) => page.all(card, test).map((e) => e.getAttribute("aria-label"));
      console.log(JSON.stringify({
        select: label((e) => e.tagName === "select"),
        range: label((e) => e.tagName === "input" && e.type === "range"),
        number: label((e) => e.tagName === "input" && e.type === "number"),
        button: label((e) => e.tagName === "button" && !e.className),
      }));
    """)
    assert found == {
        "select": ["Varmvattenprogram"],
        "range": ["Max varvtal"],
        # A control without a name is named after its entity.
        "number": ["Rumsbörvärde", "number.unnamed"],
        "button": ["Tryck: Släpp all styrning"],
    }


def test_a_note_left_open_before_a_new_configuration_opens_with_the_first_click():
    # F3.7: what is open was kept per entity and outlived setConfig, which
    # draws the notes anew and closed, so the first click closed a note that
    # was already closed and only the second opened it.
    found = _node(f"""
      const config = {{rows: {json.dumps(ROWS)}, explain: "Förklaring"}};
      const card = page.mountCard(CARD, "ctc-ecozenith-rows", config, STATES);
      by(card, "why")[0].fire("click");
      const before = !by(card, "note")[0].hidden;
      card.setConfig(config);
      const drawn = {{open: !by(card, "note")[0].hidden,
                     expanded: aria(by(card, "why")[0]).expanded}};
      by(card, "why")[0].fire("click");
      const first = {{open: !by(card, "note")[0].hidden,
                     expanded: aria(by(card, "why")[0]).expanded}};
      by(card, "why")[0].fire("click");
      const second = !by(card, "note")[0].hidden;
      console.log(JSON.stringify({{before, drawn, first, second}}));
    """)
    assert found == {
        "before": True,
        "drawn": {"open": False, "expanded": "false"},
        "first": {"open": True, "expanded": "true"},
        "second": False,
    }


def test_the_focus_ring_is_two_pixels_and_the_row_has_none_of_its_own():
    style = _node("""
      const card = page.mountCard(CARD, "ctc-ecozenith-rows", {rows: []}, {});
      console.log(JSON.stringify(page.styleOf(card)));
    """)
    why = re.search(r"\.why:focus-visible\s*\{([^}]*)\}", style)
    assert why and "outline: 2px solid" in why.group(1)
    link = re.search(r"\.note a:focus-visible\s*\{([^}]*)\}", style)
    assert link and "outline: 2px solid" in link.group(1)
    assert ".row:focus-visible" not in style
