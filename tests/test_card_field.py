"""The controls card's number field, worked in node against a stub DOM.

tests/test_card.py pins the pure helpers that decide what a typed field says.
This file pins what the field does with it over time: what it sends when the
user empties it, presses Enter, leaves it, or has a write refused, while Home
Assistant keeps sending states and the pump is moved at the panel. The card is
mounted for real (tests/card_dom.js stands in for the browser), so the field's
own `shown` bookkeeping is under test, not a copy of it.

Two rules from R45 are the point. An emptied field asks for nothing, and what
goes back into it counts as shown, so the next blur never writes the pump's
own value into the control register (F7.1). A refused write leaves the field
showing the pump's value, with the refused number no longer counted as shown,
so Enter on it tries again and a blur sends nothing (F7.2).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
DOM = TESTS / "card_dom.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _play(script: str, **options) -> dict:
    """Run `script` against a mounted card, with the levers bound to `t`.

    The script is the body of an async function, so it can `await t.refuse()`
    and `await t.accept()` and let the refusal reach the field before it goes
    on. What comes back is what the field shows, what was sent, how many calls
    are still open and whether the row still looks busy.
    """
    program = (
        f"const dom = require({json.dumps(str(DOM))});\n"
        f"const t = dom.mount({json.dumps(str(CARD))}, {json.dumps(options)});\n"
        f"(async () => {{\n{script}\n"
        "console.log(JSON.stringify(t.result()));\n"
        "})().catch((err) => { console.error(err); process.exit(1); });\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# ------------------------------------------------------------ the rules kept

def test_a_tab_through_the_field_and_a_blur_after_enter_send_nothing_more():
    # 6d77647's promise: a value already shown or already sent is not sent
    # again. Tabbing through, Enter then blur, and a state that lands as typed
    # followed by another tab through are all one write at most.
    assert _play("""
      t.focus(); t.tab();
      t.focus(); t.type("22"); t.enter(); await t.accept(); t.tab();
      t.state("22.0");
      t.focus(); t.tab();
    """) == {"calls": [22], "field": "22.0", "open": 0, "pending": "0"}


# -------------------------------------------------------------------- F7.1

def test_a_value_moved_at_the_panel_is_not_sent_back_after_the_field_is_emptied():
    # The field is held at 21.5 when the setpoint is turned to 25 at the panel.
    # The user empties the field and presses Enter, which puts 25.0 back in;
    # leaving the field must not write that 25 to 1010 as if they typed it.
    assert _play("""
      t.focus();
      t.state("25.0");
      t.type(""); t.enter();
      if (t.field.value !== "25.0") throw new Error("Enter refilled " + t.field.value);
      t.tab();
    """) == {"calls": [], "field": "25.0", "open": 0, "pending": "0"}


def test_a_write_just_taken_is_not_undone_by_emptying_the_field():
    # 22 is sent and taken, but Home Assistant has not reported the new state
    # yet. Emptying the field and pressing Enter brings 21.5 back from the
    # state; leaving must not send that 21.5 and undo the write.
    assert _play("""
      t.focus(); t.type("22"); t.enter(); await t.accept();
      t.type(""); t.enter();
      t.tab();
    """) == {"calls": [22], "field": "21.5", "open": 0, "pending": "1"}


def test_a_zero_typed_after_an_empty_state_is_sent():
    # The compressor's top speed as a field, with no state to show: the field
    # is "" after Enter on it, and Number("") would be 0. A 0 typed on purpose
    # afterwards is a value, not a repeat of what is shown.
    assert _play(
        """
      t.focus(); t.enter();
      t.type("0"); t.enter(); await t.accept();
    """,
        entity="number.ctc_max_rps",
        state="unknown",
        attributes={"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps"},
        widget="field",
    ) == {"calls": [0], "field": "0", "open": 0, "pending": "1"}


# -------------------------------------------------------------------- F7.2

def test_enter_on_a_refused_value_tries_again_and_the_field_shows_the_pumps_value():
    # A write refused for a passing reason (the Modbus way busy, a timeout):
    # the field goes back to the pump's value at once, the row stops looking
    # busy, and Enter on the same number is a new attempt rather than dead.
    assert _play("""
      t.focus(); t.type("22"); t.enter(); await t.refuse();
      const after = t.result();
      t.type("22"); t.enter(); await t.accept();
      if (after.field !== "21.5" || after.pending !== "0") throw new Error(JSON.stringify(after));
    """) == {"calls": [22, 22], "field": "22", "open": 0, "pending": "1"}


def test_a_refusal_does_not_turn_the_next_blur_into_a_write():
    # 5 is refused (under min 10). Neither leaving the field straight away nor
    # emptying it, pressing Enter and leaving sends the pump's own 21.5.
    assert _play("""
      t.focus(); t.type("5"); t.enter(); await t.refuse(); t.tab();
      t.focus(); t.type("5"); t.enter(); await t.refuse();
      t.type(""); t.enter(); t.tab();
    """) == {"calls": [5, 5], "field": "21.5", "open": 0, "pending": "0"}


def test_a_refusal_leaves_a_number_typed_since_alone():
    # The user has already typed 23 when the refusal of 22 comes back: 23 is
    # not overwritten, and Enter sends it.
    assert _play("""
      t.focus(); t.type("22"); t.enter();
      t.type("23");
      await t.refuse();
      const kept = t.field.value;
      t.enter(); await t.accept();
      if (kept !== "23") throw new Error("the refusal overwrote " + kept);
    """) == {"calls": [22, 23], "field": "23", "open": 0, "pending": "1"}


def test_a_refusal_arriving_after_the_field_was_left_clears_the_refused_number():
    # Enter, then Tab, then the refusal: the field is not held, but no state
    # has come in to correct it, so the refused 22 would stay on the screen as
    # if taken. It goes back to 21.5, and a tab through afterwards sends nothing.
    assert _play("""
      t.focus(); t.type("22"); t.enter(); t.tab();
      await t.refuse();
      t.focus(); t.tab();
    """) == {"calls": [22], "field": "21.5", "open": 0, "pending": "0"}
