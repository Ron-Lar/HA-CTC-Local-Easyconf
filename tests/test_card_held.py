"""What Home Assistant holds, at a glance, on the controls and in the list (R49, the card's half).

The numbers carried "styrning aktiv" but the card did not show it: a held row
looked like a mirrored one, and a number was let go of only with Släpp all
styrning. Now the controls card and the list mark a held control "Styrd" in
the theme's primary colour, with what holding means as its hover text, show
"pumpen själv: <value>" from the attribute pump_value where the integration
gives it, and the controls card gives a held number a button that calls the
integration's service release for that one entity. A list lets go by its own
option, Släpp styrningen, and gets no button.

The integration's side (the attributes on the list, pump_value and the
service) is built in another track at the same time; this file works the card
against stand-in states with exactly those names: control_active, with
"styrning aktiv" as the older name, and pump_value, with "pumpens värde".
Worked in node on the stub DOM (tests/card_page.js over tests/card_dom.js).
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

WORDS = {
    "controlled": "Styrd",
    "controlled_note": "Home Assistant håller värdet och skriver om det varje minut.",
    "pump_value": "pumpen själv",
    "release": "Släpp",
    "no_limit": "ingen gräns",
    "unset": "ej satt",
}
RPS = "number.ctc_max_rps"
MODE = "select.ctc_dhw_mode"
RANGE = {"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps"}
MODES = ["Släpp styrningen", "Ekonomi", "Normal", "Komfort", "Manuell"]


def _number(state: str, **attributes) -> dict:
    return {"state": state, "attributes": {**RANGE, **attributes}}


def _mode(state: str, **attributes) -> dict:
    return {"state": state, "attributes": {"options": MODES, **attributes}}


def _play(card: str, states: dict, script: str = "", items: list | None = None) -> dict:
    """Mount `card` over `states`, run `script`, and read each row's marks.

    `t.state(states)` sends new states; every service call is written down
    and stays open until `t.accept()` or `t.refuse()`. What comes back per
    row: whether "Styrd" shows and its hover text, the pump's own value line
    (null while hidden), and the release button (null where there is none).
    """
    items = items or [
        {"entity": RPS, "name": "Max varvtal", "explanation": "Taket.", "zero_means": "no_limit"},
        {"entity": MODE, "name": "Varmvattenprogram", "explanation": "Programmet."},
    ]
    field = "rows" if card == "ctc-ecozenith-rows" else "items"
    config = {field: items, "states": WORDS}
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const calls = [];
      const open = [];
      const hass = (states) => ({{
        states,
        callService: (domain, service, data) => {{
          calls.push([domain, service, data]);
          return new Promise((resolve, reject) => open.push({{ resolve, reject }}));
        }},
      }});
      const card = page.mountCard({json.dumps(str(CARD))}, {json.dumps(card)}, {json.dumps(config)});
      card.hass = hass({json.dumps(states)});
      const rowClass = {json.dumps("control" if card == "ctc-ecozenith-controls" else "row")};
      const answer = async (verdict) => {{
        const call = open.shift();
        if (verdict === "accept") call.resolve(); else call.reject(new Error("refused"));
        await new Promise((resolve) => setImmediate(resolve));
      }};
      const t = {{
        card, calls,
        state: (states) => {{ card.hass = hass(states); }},
        accept: () => answer("accept"),
        refuse: () => answer("refuse"),
        release: () => page.all(card, (e) => e.className === "release")[0],
      }};
      const read = () => page.all(card, (e) => e.className === rowClass).map((row) => {{
        const find = (cls) => page.all({{shadowRoot: row}}, (e) => e.className === cls)[0];
        const mark = find("held");
        const pump = find("pump");
        const release = find("release");
        return {{
          held: mark ? !mark.hidden : null,
          title: mark ? mark.title || null : null,
          mark: mark ? mark.textContent : null,
          pump: pump && !pump.hidden ? pump.textContent : null,
          release: release ? {{shown: !release.hidden, disabled: release.disabled,
                               text: release.textContent,
                               label: release.getAttribute("aria-label")}} : null,
        }};
      }});
      (async () => {{
        {script}
        console.log(JSON.stringify({{rows: read(), calls}}));
      }})().catch((err) => {{ console.error(err); process.exit(1); }});
    """
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


HELD = {
    RPS: _number("40.0", control_active=True, pump_value=50),
    MODE: _mode("Komfort", control_active=True, pump_value="Normal"),
}
IDLE = {
    RPS: _number("50.0", control_active=False),
    MODE: _mode("Normal", control_active=False, pump_value="Normal"),
}


# ------------------------------------------------------------ the controls


def test_a_held_number_is_marked_shows_the_pumps_own_value_and_can_be_let_go_of():
    found = _play("ctc-ecozenith-controls", HELD)
    number, mode = found["rows"]
    assert number == {
        "held": True, "title": WORDS["controlled_note"], "mark": "Styrd",
        "pump": "pumpen själv: 50 rps",
        "release": {"shown": True, "disabled": False, "text": "Släpp",
                    "label": "Släpp: Max varvtal"},
    }
    # The list is marked and shows the pump's own mode, and lets go by its
    # own option: no button.
    assert mode == {
        "held": True, "title": WORDS["controlled_note"], "mark": "Styrd",
        "pump": "pumpen själv: Normal", "release": None,
    }


def test_the_release_button_calls_the_service_for_that_one_entity_and_goes_with_the_hold():
    found = _play("ctc-ecozenith-controls", HELD, """
      t.release().fire("click");
      const busy = t.release().disabled;
      if (!busy) throw new Error("the button can be pressed twice");
      await t.accept();
      t.state(%s);
    """ % json.dumps(IDLE))
    assert found["calls"] == [["ctc_ecozenith", "release", {"entity_id": RPS}]]
    number, mode = found["rows"]
    # Let go of: no mark, no line, and the button hidden but ready again.
    assert number == {
        "held": False, "title": WORDS["controlled_note"], "mark": "Styrd", "pump": None,
        "release": {"shown": False, "disabled": False, "text": "Släpp",
                    "label": "Släpp: Max varvtal"},
    }
    # The list at rest stands on Släpp styrningen, so the pump's own mode is
    # said under it (F2.4).
    assert mode["held"] is False and mode["pump"] == "pumpen själv: Normal"


def test_a_refused_release_makes_the_button_ready_again():
    found = _play("ctc-ecozenith-controls", HELD, """
      t.release().fire("click");
      await t.refuse();
    """)
    assert found["rows"][0]["release"] == {
        "shown": True, "disabled": False, "text": "Släpp", "label": "Släpp: Max varvtal",
    }


def test_a_control_that_is_not_held_shows_neither_mark_nor_button():
    found = _play("ctc-ecozenith-controls", IDLE)
    number, mode = found["rows"]
    assert (number["held"], number["pump"], number["release"]["shown"]) == (False, None, False)
    # The list at rest stands on Släpp styrningen rather than on the pump's
    # own mode, which it could not pick there (F2.4), so the pump's mode is
    # said under it.
    assert (mode["held"], mode["pump"]) == (False, "pumpen själv: Normal")


def test_a_list_at_rest_whose_value_is_not_the_pumps_says_the_pumps_value():
    # An older integration shows "Släpp styrningen" at rest although the pump
    # runs Normal: the line says what the pump does.
    found = _play("ctc-ecozenith-controls", {
        RPS: _number("50.0"), MODE: _mode("Släpp styrningen", pump_value="Normal"),
    })
    assert found["rows"][1]["held"] is False
    assert found["rows"][1]["pump"] == "pumpen själv: Normal"


def test_the_older_swedish_names_are_read_too():
    found = _play("ctc-ecozenith-controls", {
        RPS: _number("40.0", **{"styrning aktiv": "ja", "pumpens värde": 50}),
        MODE: _mode("Komfort", **{"styrning aktiv": "ja"}),
    })
    number, mode = found["rows"]
    assert (number["held"], number["pump"], number["release"]["shown"]) == (
        True, "pumpen själv: 50 rps", True,
    )
    # Held, but no pump_value from this integration: no line.
    assert (mode["held"], mode["pump"]) == (True, None)


def test_the_english_boolean_wins_over_the_swedish_word():
    found = _play("ctc-ecozenith-controls", {
        RPS: _number("40.0", control_active=False, **{"styrning aktiv": "ja"}),
        MODE: _mode("Normal"),
    })
    assert found["rows"][0]["held"] is False


def test_the_pumps_own_zero_that_means_no_limit_is_worded():
    found = _play("ctc-ecozenith-controls", {
        RPS: _number("40.0", control_active=True, pump_value=0), MODE: _mode("Normal"),
    })
    assert found["rows"][0]["pump"] == "pumpen själv: ingen gräns"


def test_a_reading_has_no_mark_at_all():
    found = _play("ctc-ecozenith-rows", {
        "sensor.ctc_outdoor": {"state": "7.5", "attributes": {"control_active": True}},
    }, items=[{"entity": "sensor.ctc_outdoor", "name": "Utetemperatur", "explanation": "Ute."}])
    assert found["rows"] == [{"held": None, "title": None, "mark": None, "pump": None, "release": None}]


# ---------------------------------------------------------------- the list


def test_the_list_marks_what_is_held_without_a_button():
    found = _play("ctc-ecozenith-rows", HELD)
    number, mode = found["rows"]
    assert number == {
        "held": True, "title": WORDS["controlled_note"], "mark": "Styrd",
        "pump": "pumpen själv: 50 rps", "release": None,
    }
    assert (mode["held"], mode["pump"]) == (True, "pumpen själv: Normal")


def test_the_list_of_values_shows_a_mode_at_rest_as_its_state_and_says_it_once():
    # Only the controls card's mode list stands on Släpp styrningen at rest.
    # The list of values writes the state, the pump's own Normal, and has
    # nothing to add under it.
    found = _play("ctc-ecozenith-rows", IDLE)
    assert (found["rows"][1]["held"], found["rows"][1]["pump"]) == (False, None)


def test_a_search_for_the_mark_finds_what_is_held():
    # The list with its search field: "styrd" finds the held number and not
    # the list at rest, and so does the pump's own value under it.
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const config = {{rows: [
        {{entity: {json.dumps(RPS)}, name: "Max varvtal", explanation: "Taket."}},
        {{entity: {json.dumps(MODE)}, name: "Varmvattenprogram", explanation: "Programmet."}},
      ], states: {json.dumps(WORDS)}, filter: "Sök"}};
      const card = page.mountCard({json.dumps(str(CARD))}, "ctc-ecozenith-rows", config,
                                  {json.dumps({**HELD, MODE: IDLE[MODE]})});
      const search = page.all(card, (e) => e.type === "search")[0];
      search.value = "styrd"; search.fire("input");
      const held = page.all(card, (e) => e.className === "row").map((r) => !r.hidden);
      search.value = "pumpen själv 50"; search.fire("input");
      const own = page.all(card, (e) => e.className === "row").map((r) => !r.hidden);
      console.log(JSON.stringify({{held, own}}));
    """
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"held": [True, False], "own": [True, False]}
