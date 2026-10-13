"""The section card, worked in node against the stub DOM (roadmap L11).

custom:ctc-ecozenith-section asks the integration for one section of the CTC
page (ctc_ecozenith/items) and draws the cards that come back with the page's
own cards, so the explanations come from the integration and are never copied
into a dashboard. These tests stand in for Home Assistant with a hass whose
callWS answers what the test says and whose connection can be told that the
page changed, and read back what the card asked, what it drew and what it
said in place of the section.
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

DEVICE = "0123456789abcdef0123456789abcdef"
STATUS = "sensor.ctc_status"
OUTDOOR = "sensor.ctc_outdoor"

CHIPS = {
    "type": "custom:ctc-ecozenith-chips",
    "items": [{"entity": STATUS, "name": "Status", "explanation": "Vad styrenheten gör.",
               "source": "Modbus 62005"}],
    "more_info": "Mer info",
    "explain": "Förklaring",
    "states": {"on": "Till", "off": "Av"},
    "grid_options": {"columns": "full"},
}
ROWS = {
    "type": "custom:ctc-ecozenith-rows",
    "rows": [{"entity": OUTDOOR, "name": "Utetemperatur", "explanation": "Givaren ute.",
              "source": "Modbus 62000", "hide_unavailable": True}],
    "more_info": "Mer info",
    "explain": "Förklaring",
    "states": {},
    "grid_options": {"columns": "full"},
    "visibility": [{"condition": "state", "entity": OUTDOOR,
                    "state_not": ["unavailable", "unknown"]}],
}

#: Mounts the section card on a stand-in hass. `answer(x)` queues what the next
#: callWS answers: {cards: [...]} or {error: {code, message}}. `asked` is every
#: request, `seen()` what the card shows: the cards it drew (their element names
#: and configs), the note it wrote, and whether it hides itself.
PRELUDE = f"""
const page = require({json.dumps(str(PAGE))});
document.documentElement = {{ lang: "sv" }};
require({json.dumps(str(CARD))});
const Section = customElements.get("ctc-ecozenith-section");
const tagOf = (element) => Object.entries({{
  chips: "ctc-ecozenith-chips", readings: "ctc-ecozenith-readings",
  controls: "ctc-ecozenith-controls", rows: "ctc-ecozenith-rows",
}}).map(([, tag]) => tag).find((tag) => element instanceof customElements.get(tag)) || null;
const asked = [];
const queue = [];
const answer = (next) => queue.push(next);
let listener = null;
let unsubscribed = 0;
let language = "sv";
const hass = (states) => ({{
  states, language,
  callService: () => Promise.resolve(),
  callWS: (request) => {{
    asked.push(request);
    const next = queue.shift() || {{ cards: [] }};
    return next.error ? Promise.reject(next.error) : Promise.resolve(next);
  }},
  connection: {{
    subscribeEvents: (callback, type) => {{
      listener = {{ callback, type }};
      return Promise.resolve(() => {{ unsubscribed += 1; }});
    }},
  }},
}});
const tick = async () => {{
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
}};
//: Home Assistant telling the card's connection that a dashboard changed.
const changed = (url_path) => listener.callback({{
  event_type: "lovelace_updated", data: {{ url_path }},
}});
const card = new Section();
const stack = () => card.shadowRoot.children.find((e) => e.className === "stack");
const note = () => card.shadowRoot.children.find((e) => e.className === "message");
const seen = () => ({{
  cards: stack().children.map((e) => ({{ tag: tagOf(e), hidden: e.hidden, config: e._config }})),
  stackHidden: stack().hidden,
  note: note().hidden ? null : note().textContent,
  hidden: Boolean(card.hidden),
}});
const print = (value) => console.log(JSON.stringify(value));
"""


def _run(script: str):
    program = PRELUDE + "(async () => {\n" + script + "\n})().catch((err) => {" \
        " console.error(err && err.stack || err); process.exit(1); });\n"
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _state(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


STATES = {STATUS: _state("Redo för start", device_class="enum"), OUTDOOR: _state("7.2")}
HEATING = {**STATES, STATUS: _state("Värme")}


def test_the_card_asks_for_its_section_and_draws_it_with_the_pages_own_card():
    found = _run(f"""
        answer({{ cards: [{json.dumps(CHIPS)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "status" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        const chip = () => stack().children[0];
        const value = () => page.all(chip(), (e) => e.className === "value")[0].textContent;
        const before = value();
        card.hass = hass({json.dumps(HEATING)});
        print({{ asked, seen: seen(), before, after: value() }});
    """)
    assert found["asked"] == [
        {"type": "ctc_ecozenith/items", "device_id": DEVICE, "section": "status"},
    ]
    (drawn,) = found["seen"]["cards"]
    assert drawn["tag"] == "ctc-ecozenith-chips"
    assert drawn["config"] == CHIPS, "kortet får sidans egen konfiguration, ingen egen text"
    assert found["seen"]["note"] is None and not found["seen"]["hidden"]
    # A new state reaches the page's card through the section card.
    assert (found["before"], found["after"]) == ("Redo för start", "Värme")


def test_an_answer_like_the_last_leaves_the_card_alone_and_a_new_one_is_drawn():
    found = _run(f"""
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.isConnected = true;
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "temperatures" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        const first = stack().children[0];
        // The page changed: lovelace_updated names its address, and the card asks again.
        answer({{ cards: [{json.dumps(ROWS)}] }});
        changed("ctc-ecozenith");
        await tick();
        const kept = stack().children[0] === first;
        // Another dashboard changed: nothing to ask.
        changed("lovelace");
        await tick();
        const quiet = asked.length;
        const rows = {json.dumps(ROWS)};
        rows.rows[0].name = "Ute";
        answer({{ cards: [rows] }});
        changed("ctc-ecozenith");
        await tick();
        const replaced = stack().children[0] !== first;
        card.disconnectedCallback();
        await tick();
        print({{ type: listener.type, kept, quiet, replaced, asked: asked.length, unsubscribed,
                 name: stack().children[0]._config.rows[0].name }});
    """)
    assert found["type"] == "lovelace_updated"
    assert found["kept"], "samma svar ritas inte om, öppna noter och sökningen står kvar"
    assert found["quiet"] == 2, "en annan instrumentpanel är inte sidan"
    assert found["replaced"] and found["name"] == "Ute"
    assert found["asked"] == 3
    assert found["unsubscribed"] == 1


def test_a_new_section_in_the_configuration_is_asked_for_and_a_late_answer_is_dropped():
    found = _run(f"""
        let release;
        const late = new Promise((resolve) => {{ release = resolve; }});
        const h = hass({json.dumps(STATES)});
        const ws = h.callWS;
        let first = true;
        h.callWS = (request) => {{
          if (first) {{ first = false; asked.push(request); return late; }}
          return ws(request);
        }};
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "status" }});
        card.hass = h;
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "temperatures" }});
        await tick();
        release({{ cards: [{json.dumps(CHIPS)}] }});
        await tick();
        print({{ sections: asked.map((r) => r.section), tags: seen().cards.map((c) => c.tag) }});
    """)
    assert found["sections"] == ["status", "temperatures"]
    assert found["tags"] == ["ctc-ecozenith-rows"], "svaret på den gamla frågan ritas inte"


@pytest.mark.parametrize(
    ("language", "error", "said"),
    [
        ("sv", {"code": "not_running", "message": "The heat pump is not running"},
         "Värmepumpen är inte igång just nu. Kortet fylls i när integrationen har startat."),
        ("en", {"code": "not_found", "message": "The device is not a heat pump"},
         "The device is not a heat pump of CTC Local Easyconf."),
        ("sv", {"code": "unknown_command", "message": "Unknown command."},
         "Kortet är nyare än integrationen som körs. "
         "Starta om Home Assistant efter uppdateringen."),
        ("sv", {"code": "unknown_error", "message": "Unknown error"}, "Unknown error"),
    ],
)
def test_a_refusal_is_said_in_place_of_the_section(language, error, said):
    found = _run(f"""
        language = {json.dumps(language)};
        answer({{ error: {json.dumps(error)} }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "energy" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        print(seen());
    """)
    assert found == {"cards": [], "stackHidden": True, "note": said, "hidden": False}


def test_after_a_refusal_the_card_asks_again_but_not_at_once():
    found = _run(f"""
        answer({{ error: {{ code: "not_running", message: "not running" }} }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "temperatures" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        card.hass = hass({json.dumps(STATES)});
        await tick();
        const soon = asked.length;
        const now = Date.now;
        Date.now = () => now() + 31000;
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        Date.now = now;
        print({{ soon, later: asked.length, seen: seen() }});
    """)
    assert found["soon"] == 1, "inte en fråga per tillståndsändring"
    assert found["later"] == 2
    assert found["seen"]["note"] is None and found["seen"]["stackHidden"] is False
    assert [c["tag"] for c in found["seen"]["cards"]] == ["ctc-ecozenith-rows"]


def test_the_card_is_hidden_as_the_page_hides_it_and_says_so_only_in_the_editor():
    unavailable = {**STATES, OUTDOOR: _state("unavailable")}
    found = _run(f"""
        const events = [];
        card.dispatchEvent = (event) => events.push([event.type, event.bubbles, event.composed]);
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "temperatures" }});
        card.hass = hass({json.dumps(unavailable)});
        await tick();
        const gone = seen();
        card.preview = true;
        const editing = seen();
        card.preview = false;
        card.hass = hass({json.dumps(STATES)});
        const back = seen();
        print({{ gone, editing, back, events }});
    """)
    assert found["gone"]["hidden"] is True and found["gone"]["cards"][0]["hidden"] is True
    # Lovelace hears of every change, and only of a change: hidden, shown in
    # the editor, hidden again when the editor closes, shown with a value.
    assert found["events"] == [["card-visibility-changed", True, True]] * 4
    assert found["editing"]["hidden"] is False
    assert found["editing"]["note"] == (
        "Sidan visar inget i den här sektionen för den här värmepumpen just nu."
    )
    assert found["back"]["hidden"] is False and found["back"]["cards"][0]["hidden"] is False
    assert found["back"]["note"] is None


def test_what_home_assistant_holds_is_seen_only_while_the_count_is_above_zero():
    # The page shows the overrides section (R49) only while the sensor counts
    # anything held, with a numeric state condition; the section card keeps
    # the same rule rather than showing a chip that says 0.
    held = "sensor.ctc_overrides"
    rule = [{"condition": "numeric_state", "entity": held, "above": 0}]
    chips = {**CHIPS, "items": [{"entity": held, "name": "Aktiva överstyrningar"}],
             "visibility": rule}
    found = _run(f"""
        answer({{ cards: [{json.dumps(chips)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "overrides" }});
        const counted = (state) => ({{ ...{json.dumps(STATES)}, [{json.dumps(held)}]: {{
          state, attributes: {{}} }} }});
        card.hass = hass(counted("0"));
        await tick();
        const none = seen();
        card.hass = hass(counted("2"));
        const two = seen();
        card.hass = hass(counted("unavailable"));
        const lost = seen();
        card.hass = hass({json.dumps(STATES)});
        const missing = seen();
        print({{ none, two, lost, missing }});
    """)
    assert found["none"]["hidden"] is True and found["none"]["cards"][0]["hidden"] is True
    assert found["two"]["hidden"] is False and found["two"]["cards"][0]["hidden"] is False
    assert found["lost"]["hidden"] is True
    assert found["missing"]["hidden"] is True


def test_an_empty_section_draws_nothing_and_a_card_the_section_card_cannot_draw_is_left_out():
    found = _run(f"""
        answer({{ cards: [] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "heating_controls" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        const empty = seen();
        answer({{ cards: [{{ type: "markdown", content: "x" }}, {json.dumps(CHIPS)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "status" }});
        await tick();
        print({{ empty, mixed: seen().cards.map((c) => c.tag), size: card.getCardSize() }});
    """)
    assert found["empty"] == {"cards": [], "stackHidden": False, "note": None, "hidden": True}
    assert found["mixed"] == ["ctc-ecozenith-chips"]
    assert found["size"] >= 1


def test_shown_again_the_card_asks_again_but_not_twice_in_half_a_minute():
    found = _run(f"""
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "temperatures" }});
        card.hass = hass({json.dumps(STATES)});
        card.connectedCallback();
        await tick();
        const mounted = asked.length;
        const now = Date.now;
        Date.now = () => now() + 31000;
        answer({{ cards: [{json.dumps(ROWS)}] }});
        card.connectedCallback();
        await tick();
        Date.now = now;
        print({{ mounted, shown: asked.length }});
    """)
    assert found == {"mounted": 1, "shown": 2}


def test_a_card_that_will_not_take_the_pages_config_is_said_rather_than_thrown():
    found = _run(f"""
        answer({{ cards: [{{ type: "custom:ctc-ecozenith-rows" }}] }});
        card.setConfig({{ device_id: {json.dumps(DEVICE)}, section: "values" }});
        card.hass = hass({json.dumps(STATES)});
        await tick();
        print(seen());
    """)
    assert found == {
        "cards": [], "stackHidden": True, "note": "ctc-ecozenith-rows needs rows", "hidden": False,
    }
