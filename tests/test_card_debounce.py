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

The seams with R49 are pinned here too. The hot water program's state at rest
is the pump's own mode, so the list at rest stands on Släpp styrningen with
the pump's mode under it: every mode is then a change, and after Släpp
styrningen the row lands at once (F2.4, F5.2). A change that waits keeps the
control whether or not it has the focus (F3.2), down and up again at rest
sends nothing (F3.4), and a refused mode goes back out of the list (F3.6).
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

#: The hot water program's options as select.py publishes them.
MODES = ["Släpp styrningen", "Ekonomi", "Normal", "Komfort", "Manuell"]
#: A list that says nothing about being held, as another integration's would:
#: it shows its state, as any list does.
SELECT = ("select.dhw_mode", "Släpp styrningen", {"options": MODES})
#: The hot water program as select.py publishes it at rest since R49: the
#: state is the pump's own mode out of 61500, and nothing is held.
DHW = ("select.dhw_mode", "Normal",
       {"options": MODES, "control_active": False, "pump_value": "Normal"})
#: The same list while Home Assistant holds Komfort.
DHW_HELD = ("select.dhw_mode", "Komfort",
            {"options": MODES, "control_active": True, "pump_value": "Normal"})
#: The compressor's top speed, a slider of 120 steps.
SLIDER = ("number.max_rps", "50", {"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps"})
#: The same at rest and held, with the attribute number.py gives it.
SLIDER_IDLE = (SLIDER[0], "50.0", {**SLIDER[2], "control_active": False})
SLIDER_HELD = (SLIDER[0], "50.0", {**SLIDER[2], "control_active": True, "pump_value": 60})
#: The room setpoint, a field: 200 steps are too many for a slider.
FIELD = ("number.room", "21.5", {"min": 10, "max": 30, "step": 0.1, "unit_of_measurement": "°C"})


def _play(script: str, control: tuple) -> dict:
    """Mount the controls card around one control and run `script` on it.

    `t.step(value)` is one arrow key: the value changes and change fires,
    with the focus on the control as the keyboard leaves it. `t.blur()`
    takes the focus away. `t.wait(ms)` lets the card's timers run.
    `t.state(state, attributes)` sends a new state, with `attributes` added
    to the control's own. Every service call stays open until `t.accept()`
    or `t.refuse()` answers the oldest. result() is what was sent, what the
    control shows and whether the row looks busy; what the script returns,
    if anything, is printed in its place. `t.pump()` is the line under the
    row (null while hidden), `t.held()` whether Styrd shows.
    """
    entity, state, attributes = control
    config = {
        "items": [{"entity": entity, "name": "Reglage", "explanation": "Vad det är."}],
        "states": {"pump_value": "pumpen själv", "controlled": "Styrd"},
    }
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const sleep = require("timers/promises").setTimeout;
      const calls = [];
      const open = [];
      const attributes = {json.dumps(attributes)};
      const hass = (state, extra = {{}}) => ({{
        states: {{ {json.dumps(entity)}: {{ state, attributes: {{ ...attributes, ...extra }} }} }},
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
        blur: () => control.blur(),
        wait: (ms) => sleep(ms),
        state: (state, extra) => {{ card.hass = hass(state, extra); }},
        pump: () => {{
          const line = page.all(card, (e) => e.className === "pump")[0];
          return line && !line.hidden ? line.textContent : null;
        }},
        held: () => {{
          const mark = page.all(card, (e) => e.className === "held")[0];
          return Boolean(mark) && !mark.hidden;
        }},
        accept: () => answer("accept"),
        refuse: () => answer("refuse"),
        result: () => ({{
          calls: [...calls], value: control.value, pending: widget.dataset.pending,
        }}),
      }};
      (async () => {{
        const out = await (async () => {{
          {script}
        }})();
        console.log(JSON.stringify(out === undefined ? t.result() : out));
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
      if (meanwhile.calls.length || meanwhile.pending !== "1")
        throw new Error(JSON.stringify(meanwhile));
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


# ------------------------------------------- the list at rest (R49 with R47)


def test_the_list_at_rest_stands_on_release_so_the_pumps_own_mode_can_be_held():
    # F2.4: the state at rest is the pump's own mode, Normal. A list standing
    # on Normal could not pick it, since choosing the option a list already
    # shows fires no change. At rest it stands on Släpp styrningen with the
    # pump's mode under it, so Normal is a change like any other, is written
    # and lands: held, the row no longer busy.
    found = _play("""
      const rest = {value: t.control.value, pump: t.pump(), held: t.held()};
      t.step("Normal"); await t.wait(450); await t.accept();
      t.state("Normal", {control_active: true});
      return {rest, ...t.result(), pump: t.pump(), held: t.held()};
    """, DHW)
    assert found == {
        "rest": {"value": "Släpp styrningen", "pump": "pumpen själv: Normal", "held": False},
        "calls": ["Normal"], "value": "Normal", "pending": "0",
        "pump": "pumpen själv: Normal", "held": True,
    }


def test_release_from_a_held_mode_lands_at_once_with_the_pumps_mode_under_it():
    # F2.4 and F5.2: Komfort is held and Släpp styrningen is picked with the
    # mouse, so the list keeps the focus. The state that follows is the
    # pump's own Normal, not held. The list stands on Släpp styrningen, which
    # is what it should show at rest, so the row stops looking busy at once
    # rather than six seconds later, and Normal is said under it.
    found = _play("""
      t.step("Släpp styrningen"); await t.wait(450); await t.accept();
      t.state("Normal", {control_active: false});
      return {...t.result(), pump: t.pump(), held: t.held()};
    """, DHW_HELD)
    assert found == {
        "calls": ["Släpp styrningen"], "value": "Släpp styrningen", "pending": "0",
        "pump": "pumpen själv: Normal", "held": False,
    }


def test_a_list_at_rest_whose_mirror_is_unread_stands_on_release_with_no_line():
    # 61500 could not be read: the state is unknown and there is no pump's
    # own mode to say. The list still stands on Släpp styrningen.
    found = _play("""
      return {value: t.control.value, pump: t.pump(), pending: t.widget.dataset.pending};
    """, ("select.dhw_mode", "unknown",
          {"options": MODES, "control_active": False, "pump_value": None}))
    assert found == {"value": "Släpp styrningen", "pump": None, "pending": "0"}


# ------------------------------------------ a change that waits (F3.2)


def test_a_list_that_lost_the_focus_keeps_its_change_while_it_waits():
    # Komfort is picked and the focus leaves at once, a phone's picker closed
    # with Done. A state that comes before the wait has run out must not put
    # the list back on Släpp styrningen, or the user picks Komfort again.
    found = _play("""
      t.step("Komfort"); t.blur();
      t.state("Normal", {control_active: false});
      const meanwhile = t.result();
      await t.wait(450); await t.accept();
      t.state("Komfort", {control_active: true});
      return {meanwhile, after: t.result()};
    """, DHW)
    assert found == {
        "meanwhile": {"calls": [], "value": "Komfort", "pending": "1"},
        "after": {"calls": ["Komfort"], "value": "Komfort", "pending": "0"},
    }


def test_a_slider_without_the_focus_keeps_its_change_while_it_waits():
    # Safari and iOS give a range input no focus for a tap: the knob must not
    # jump back to the pump's 50 while 60 waits to go out.
    found = _play("""
      t.step("60"); t.blur();
      t.state("50");
      const meanwhile = t.result();
      await t.wait(450);
      return {meanwhile, after: t.result()};
    """, SLIDER)
    assert found == {
        "meanwhile": {"calls": [], "value": "60", "pending": "1"},
        "after": {"calls": [60], "value": "60", "pending": "1"},
    }


# ---------------------------------- down and up again at rest (F3.4)


def test_down_and_up_again_on_a_list_at_rest_writes_nothing():
    # Ekonomi and back to Släpp styrningen within the wait: sending it would
    # ask for nothing and only make Home Assistant hold the mode.
    assert _play("""
      t.step("Ekonomi"); await t.wait(100); t.step("Släpp styrningen");
      await t.wait(450);
    """, DHW) == {"calls": [], "value": "Släpp styrningen", "pending": "0"}


def test_right_and_left_again_on_a_slider_at_rest_writes_nothing():
    assert _play("""
      t.step("51"); t.step("50");
      await t.wait(450);
    """, SLIDER_IDLE) == {"calls": [], "value": "50", "pending": "0"}


def test_a_held_value_is_sent_even_where_it_already_stands():
    # Held, the state is what Home Assistant holds: the user may mean to go
    # on holding it, and that is a write as it always was.
    assert _play("""
      t.step("51"); t.step("50");
      await t.wait(450);
    """, SLIDER_HELD)["calls"] == [50]


def test_back_to_release_while_an_earlier_write_is_on_its_way_is_sent():
    # Komfort went out and Home Assistant has not answered: the state still
    # says nothing is held, but Komfort is about to be. Släpp styrningen must
    # follow it, or Komfort would be held against the user's last choice.
    assert _play("""
      t.step("Komfort"); await t.wait(450);
      t.step("Släpp styrningen"); await t.wait(450);
    """, DHW)["calls"] == ["Komfort", "Släpp styrningen"]


# ----------------------------------------------- a refused mode (F3.6)


def test_a_refused_mode_goes_back_out_of_a_list_that_keeps_the_focus():
    # Manuell is picked with the keyboard, which leaves the focus on the
    # list, and refused. The list goes back to what it should show, and stays
    # there when the next state comes.
    found = _play("""
      t.step("Manuell"); await t.wait(450); await t.refuse();
      const refused = t.result();
      t.state("Normal", {control_active: false});
      return {refused, after: t.result()};
    """, DHW)
    assert found == {
        "refused": {"calls": ["Manuell"], "value": "Släpp styrningen", "pending": "0"},
        "after": {"calls": ["Manuell"], "value": "Släpp styrningen", "pending": "0"},
    }


def test_a_refused_mode_of_a_held_list_goes_back_to_the_mode_held():
    assert _play("""
      t.step("Manuell"); await t.wait(450); await t.refuse();
    """, DHW_HELD) == {"calls": ["Manuell"], "value": "Komfort", "pending": "0"}
