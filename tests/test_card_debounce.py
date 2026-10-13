"""The controls card writes where the keyboard stops, not every step (R47).

A closed list fires change at every arrow key, and a slider does the same, so
whoever stepped through the hot water program with the keyboard wrote every
mode on the way, Manuell and Av included, each a Modbus write with six seconds
of busy mark. The list and the slider now wait 400 ms after a change, a newer
change takes the older one's place, and only the last value goes to the pump.
The number field is not touched: it already writes on Enter and on leaving it.

Worked in node on the stub DOM (tests/card_page.js over tests/card_dom.js),
with the card's own timers and a real wait, so what is pinned is the card's
bookkeeping: what is sent, when, and how the busy mark and the protection
against incoming states behave while a change waits.
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

#: The hot water program as select.py publishes it.
MODES = ["Släpp styrningen", "Ekonomi", "Normal", "Komfort", "Manuell"]
SELECT = ("select.dhw_mode", "Släpp styrningen", {"options": MODES})
#: The compressor's top speed, a slider of 120 steps.
SLIDER = ("number.max_rps", "50", {"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps"})
#: The room setpoint, a field: 200 steps are too many for a slider.
FIELD = ("number.room", "21.5", {"min": 10, "max": 30, "step": 0.1, "unit_of_measurement": "°C"})


def _play(script: str, control: tuple) -> dict:
    """Mount the controls card around one control and run `script` on it.

    `t.step(value)` is one arrow key: the value changes and change fires,
    with the focus on the control as the keyboard leaves it. `t.wait(ms)`
    lets the card's timers run. Every service call stays open until
    `t.accept()` or `t.refuse()` answers the oldest. result() is what was
    sent, what the control shows and whether the row looks busy.
    """
    entity, state, attributes = control
    config = {"items": [{"entity": entity, "name": "Reglage", "explanation": "Vad det är."}]}
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const sleep = require("timers/promises").setTimeout;
      const calls = [];
      const open = [];
      const attributes = {json.dumps(attributes)};
      const hass = (state) => ({{
        states: {{ {json.dumps(entity)}: {{ state, attributes }} }},
        callService: (domain, service, data) => {{
          calls.push(data.option !== undefined ? data.option : data.value);
          return new Promise((resolve, reject) => open.push({{ resolve, reject }}));
        }},
      }});
      const card = page.mountCard({json.dumps(str(CARD))}, "ctc-ecozenith-controls",
                                  {json.dumps(config)});
      card.hass = hass({json.dumps(state)});
      const widget = page.all(card, (e) => e.className === "widget")[0];
      const control = page.all(card, (e) => e.tagName === "select" || e.tagName === "input")[0];
      const answer = async (verdict) => {{
        if (!open.length) throw new Error("nothing was sent");
        const call = open.shift();
        if (verdict === "accept") call.resolve(); else call.reject(new Error("refused"));
        await sleep(0);
      }};
      const t = {{
        card, widget, control, calls,
        step: (value) => {{ control.focus(); control.value = value; control.fire("change"); }},
        wait: (ms) => sleep(ms),
        state: (state) => {{ card.hass = hass(state); }},
        accept: () => answer("accept"),
        refuse: () => answer("refuse"),
        result: () => ({{
          calls: [...calls], value: control.value, pending: widget.dataset.pending,
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


def test_stepping_through_the_list_writes_only_where_it_stops():
    # Down three times through the hot water program: nothing is written
    # while the keys come, the row is busy from the first one, and once the
    # list has stood still only Komfort goes to the pump.
    assert _play("""
      t.step("Ekonomi"); await t.wait(100);
      t.step("Normal"); await t.wait(100);
      t.step("Komfort");
      const meanwhile = t.result();
      if (meanwhile.calls.length || meanwhile.pending !== "1") throw new Error(JSON.stringify(meanwhile));
      await t.wait(450);
    """, SELECT) == {"calls": ["Komfort"], "value": "Komfort", "pending": "1"}


def test_stepping_a_slider_with_the_arrow_keys_writes_once():
    assert _play("""
      t.step("51"); t.step("52"); t.step("53");
      await t.wait(450);
    """, SLIDER) == {"calls": [53], "value": "53", "pending": "1"}


def test_two_stops_are_two_writes():
    # A change after the wait has run out is a value of its own.
    assert _play("""
      t.step("Ekonomi"); await t.wait(450); await t.accept();
      t.step("Normal"); await t.wait(450);
    """, SELECT)["calls"] == ["Ekonomi", "Normal"]


def test_a_state_that_arrives_while_the_change_waits_leaves_the_held_list_alone():
    # Home Assistant sends states all the time. The list is still held, so the
    # old mode is not put back while the change waits, and the row stays busy
    # until the pump reports the mode that was written.
    assert _play("""
      t.step("Komfort");
      t.state("Släpp styrningen");
      const held = t.result();
      if (held.value !== "Komfort" || held.pending !== "1") throw new Error(JSON.stringify(held));
      await t.wait(450); await t.accept();
      t.state("Släpp styrningen");
      const before = t.result().pending;
      if (before !== "1") throw new Error("the old mode ended the busy mark");
      t.state("Komfort");
    """, SELECT) == {"calls": ["Komfort"], "value": "Komfort", "pending": "0"}


def test_a_refusal_of_an_older_write_does_not_throw_back_a_newer_change():
    # 60 is written and still on its way when the user moves on to 65. The
    # refusal of 60 must neither put the pump's 50 back on the slider nor end
    # the busy mark while 65 waits; then 65 goes out on its own.
    assert _play("""
      t.step("60"); await t.wait(450);
      t.step("65");
      await t.refuse();
      const after = t.result();
      if (after.value !== "65" || after.pending !== "1") throw new Error(JSON.stringify(after));
      await t.wait(450);
    """, SLIDER) == {"calls": [60, 65], "value": "65", "pending": "1"}


def test_a_refused_last_write_puts_the_slider_back_to_the_pumps_value():
    assert _play("""
      t.step("60"); await t.wait(450); await t.refuse();
    """, SLIDER) == {"calls": [60], "value": "50", "pending": "0"}


def test_the_field_still_writes_on_enter_at_once():
    # The field has no debounce: Enter is already the user saying "this is it".
    assert _play("""
      t.control.focus(); t.control.value = "22"; t.control.fire("keydown", {key: "Enter"});
    """, FIELD)["calls"] == [22]
