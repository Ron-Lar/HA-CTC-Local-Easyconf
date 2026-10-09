"""The controls card around a control without a value, worked in node (V2).

The hot water setpoint (1033) has no mirror register, so its number entity is
unknown until something has been written. The card drew a slider anyway, with
the knob where the browser puts it, in the middle, and the text "Unknown" to
the right; whoever dragged it wrote a value they never meant. The same card
said "Max varvtal 0 rps" while the pump ran at 50 rps, because 0 in the pump's
own setting means no limit, which the card did not say.

Now an unknown control is marked unset: the row carries data-unset, which the
style uses to hide the knob and dim the track, the knob is parked at the
bottom of the range rather than the middle, the reading says "ej satt", and
an "i" beside it opens the row's note, which then says that the pump does not
give out its own value here. The mark goes the moment the user starts setting
it, stays off while the write is on its way, and comes back if the write is
refused. A field gets the word as its placeholder. The max rps reads "ingen
gräns" for the pump's own 0 and "0 rps" for a 0 written through the control.

The card is mounted for real (tests/card_page.js over the stub DOM of
tests/card_dom.js), so what is pinned is the card's own bookkeeping.
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

STATES = {
    "on": "Till", "off": "Av", "problem_on": "Utlöst", "problem_off": "OK",
    "unset": "ej satt", "unset_note": "Pumpen lämnar inte ut sitt eget värde här.",
    "no_limit": "ingen gräns",
}

#: The hot water setpoint as number.py publishes it: 30 to 65 in whole degrees.
DHW = {"min": 30, "max": 65, "step": 1, "unit_of_measurement": "°C", "styrning aktiv": "nej"}
#: The compressor's top speed: 0 to 120 rps, mirrored from the stored setting.
RPS = {"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps", "styrning aktiv": "nej"}


def _play(script: str, entity: str, state: str, attributes: dict, **item) -> dict:
    """Mount the controls card around one number and run `script` on it.

    `t` holds the levers: the slider or field, the row's widget, the "i" and
    the note, a way to send a new state, and accept() and refuse() for the
    oldest open service call. result() is what the row shows and did.
    """
    config = {
        "items": [{"entity": entity, "name": "Reglage", "explanation": "Vad det är.", **item}],
        "states": STATES,
        "explain": "Förklaring",
    }
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const calls = [];
      const open = [];
      const attributes = {json.dumps(attributes)};
      const hass = (state, extra = {{}}) => ({{
        states: {{ {json.dumps(entity)}: {{ state, attributes: {{...attributes, ...extra}} }} }},
        callService: (domain, service, data) => {{
          calls.push(data.value);
          return new Promise((resolve, reject) => open.push({{ resolve, reject }}));
        }},
      }});
      const card = page.mountCard({json.dumps(str(CARD))}, "ctc-ecozenith-controls",
                                  {json.dumps(config)});
      card.hass = hass({json.dumps(state)});
      const widget = page.all(card, (e) => e.className === "widget")[0];
      const control = page.all(card, (e) => e.tagName === "input")[0];
      const reading = page.all(card, (e) => e.className === "reading")[0];
      const whys = page.all(card, (e) => e.className === "why");
      const note = page.all(card, (e) => e.className === "note")[0];
      const tick = () => new Promise((resolve) => setImmediate(resolve));
      const answer = async (verdict) => {{
        if (!open.length) throw new Error("nothing was sent");
        const call = open.shift();
        if (verdict === "accept") call.resolve(); else call.reject(new Error("refused"));
        await tick();
      }};
      const t = {{
        card, widget, control, reading, note, calls,
        why: whys[whys.length - 1],
        state: (state, extra) => {{ card.hass = hass(state, extra); }},
        drag: (value) => {{ control.value = value; control.fire("input"); }},
        release: () => control.fire("change"),
        accept: () => answer("accept"),
        refuse: () => answer("refuse"),
        result: () => ({{
          unset: widget.dataset.unset,
          pending: widget.dataset.pending,
          value: control.value,
          text: reading.textContent,
          why: !t.why.hidden,
          placeholder: control.placeholder || "",
          calls,
        }}),
      }};
      (async () => {{
        {script}
        console.log(JSON.stringify(t.result()));
      }})().catch((err) => {{ console.error(err); process.exit(1); }});
    """
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# ----------------------------------------------------------------- the slider


def test_an_unknown_slider_is_drawn_empty_and_says_not_set():
    assert _play("", "number.dhw", "unknown", DHW) == {
        "unset": "1", "pending": "0", "value": "30", "text": "ej satt", "why": True,
        "placeholder": "", "calls": [],
    }


def test_the_i_beside_not_set_opens_the_note_and_the_note_says_why():
    assert _play("""
      t.why.fire("click");
      const open = !t.note.hidden;
      const says = t.note.children.map((e) => e.textContent);
      if (!open) throw new Error("the note did not open");
      if (!says.includes(STATES_NOTE)) throw new Error("the note does not say why: " + JSON.stringify(says));
      // A value arrives and the note is opened again: the sentence is gone.
      t.why.fire("click");
      t.state("45");
      page.all(t.card, (e) => e.className === "why")[0].fire("click");
      if (t.note.children.map((e) => e.textContent).includes(STATES_NOTE)) throw new Error("still says why");
    """.replace("STATES_NOTE", json.dumps(STATES["unset_note"])), "number.dhw", "unknown", DHW) == {
        "unset": "0", "pending": "0", "value": "45", "text": "45 °C", "why": False,
        "placeholder": "", "calls": [],
    }


def test_a_value_from_the_pump_fills_the_slider_and_takes_the_mark_away():
    assert _play("""
      t.state("50");
    """, "number.dhw", "unknown", DHW) == {
        "unset": "0", "pending": "0", "value": "50", "text": "50 °C", "why": False,
        "placeholder": "", "calls": [],
    }


def test_setting_an_unset_slider_takes_the_mark_away_at_once_and_writes_what_was_meant():
    # The user drags to 40 and lets go: the mark is gone from the first move,
    # 40 is sent, and while the write is on its way a state that is still
    # unknown does not park the knob again.
    assert _play("""
      t.drag("40");
      const moved = t.result();
      if (moved.unset !== "0" || moved.text !== "40 °C") throw new Error(JSON.stringify(moved));
      t.release();
      t.state("unknown");
    """, "number.dhw", "unknown", DHW) == {
        "unset": "0", "pending": "1", "value": "40", "text": "40 °C", "why": False,
        "placeholder": "", "calls": [40],
    }


def test_a_refused_write_puts_the_slider_back_to_unset():
    assert _play("""
      t.drag("40"); t.release();
      await t.refuse();
    """, "number.dhw", "unknown", DHW) == {
        "unset": "1", "pending": "0", "value": "30", "text": "ej satt", "why": True,
        "placeholder": "", "calls": [40],
    }


def test_a_taken_write_shows_the_value_once_the_pump_reports_it():
    assert _play("""
      t.drag("40"); t.release();
      await t.accept();
      t.state("40", {"styrning aktiv": "ja"});
    """, "number.dhw", "unknown", DHW) == {
        "unset": "0", "pending": "0", "value": "40", "text": "40 °C", "why": False,
        "placeholder": "", "calls": [40],
    }


def test_a_slider_with_a_value_is_drawn_as_before():
    assert _play("", "number.dhw", "55", DHW) == {
        "unset": "0", "pending": "0", "value": "55", "text": "55 °C", "why": False,
        "placeholder": "", "calls": [],
    }


# ------------------------------------------------------------------- the field


def test_an_unknown_field_is_empty_with_the_word_as_its_placeholder():
    assert _play("", "number.dhw", "unknown", DHW, widget="field") == {
        "unset": "1", "pending": "0", "value": "", "text": "°C", "why": True,
        "placeholder": "ej satt", "calls": [],
    }
    assert _play("t.state('45');", "number.dhw", "unknown", DHW, widget="field") == {
        "unset": "0", "pending": "0", "value": "45", "text": "°C", "why": False,
        "placeholder": "", "calls": [],
    }


# ---------------------------------------------------------------- no limit


def test_the_top_speed_reads_no_limit_for_the_pumps_own_zero_and_zero_for_a_written_one():
    mirrored = _play("", "number.rps", "0.0", RPS, zero_means="no_limit")
    assert (mirrored["text"], mirrored["unset"], mirrored["value"]) == ("ingen gräns", "0", "0.0")
    written = _play(
        "t.state('0.0', {'styrning aktiv': 'ja'});", "number.rps", "0.0", RPS, zero_means="no_limit"
    )
    # No formatEntityState in the stub, so the state reads with its unit as is.
    assert written["text"] == "0.0 rps"
    running = _play("t.state('50.0');", "number.rps", "0.0", RPS, zero_means="no_limit")
    assert running["text"] == "50.0 rps"
