"""The card's logic that decides a write, run in node.

The card's pure helpers are loaded into node when it is installed (GitHub's
runners have it); the custom elements are only defined in a browser, so the
DOM part is not tested here. What is pinned is what decides whether a control
writes to the pump and what it sends: that an emptied field asks for nothing
and never becomes a 0, and that a number gets a slider only when its range can
be aimed at, with the catalogue's own controls as the cases.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

CARD = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ctc_ecozenith"
    / "www"
    / "ctc-ecozenith-card.js"
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def _run(script: str):
    """Evaluate `script` in node with the card's helpers bound to `c`."""
    program = f"const c = require({json.dumps(str(CARD))});\n{script}"
    out = subprocess.run(
        ["node", "-e", program], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(out.stdout)


def _parsed(*texts: str) -> list:
    """parseFieldValue over JS literals, written as they would be in the script.

    JSON turns undefined into null, which is the one confusion this test exists
    to rule out, so an undefined answer comes back as the word instead.
    """
    calls = ", ".join(f"c.parseFieldValue({text})" for text in texts)
    return _run(
        f"console.log(JSON.stringify([{calls}]"
        '.map((v) => (v === undefined ? "undefined" : v))))'
    )


def _widgets(*ranges: tuple) -> list:
    """widgetFor over (min, max, step) triples, written as JS literals."""
    calls = ", ".join(f"c.widgetFor({a}, {b}, {s})" for a, b, s in ranges)
    return _run(f"console.log(JSON.stringify([{calls}]))")


def test_an_emptied_field_asks_for_nothing():
    # A cleared field, a field never typed in, and what a number input lets
    # through that still is no number. None of it is a 0 to send the pump.
    texts = ('""', '"   "', "null", "undefined", '"abc"', '"1e999"', '"-"', '"NaN"')
    assert _parsed(*texts) == [None] * len(texts)


def test_a_typed_number_is_taken_as_written():
    # 0 typed on purpose is a value. Nothing is clipped or rounded here: Home
    # Assistant holds min and max, and clipping would send something else.
    assert _parsed('"0"', '"21.5"', '" 22 "', '"-3"', '"130"', '"0.1"') == [
        0, 21.5, 22, -3, 130, 0.1,
    ]


def test_a_number_gets_a_slider_only_when_it_can_be_aimed_at():
    assert _widgets(
        (0, 120, 1),  # the compressor's top speed, 120 steps
        (0, 9, 0.1),  # an immersion heater limit, 90 steps
        (10, 30, 0.1),  # the room setpoint, 200 steps: a field to type in
        (0, 130, 1),  # exactly as many steps as the slider has pixels
        (0, 131, 1),
        (0, 100, "undefined"),  # no step attribute: whole steps
        ("undefined", "undefined", "undefined"),  # no range at all
        (5, 5, 1),  # no room to move
        (10, 0, 1),  # a range the wrong way round
    ) == ["slider", "slider", "field", "slider", "field", "slider", "field", "field", "field"]


def test_the_catalogues_controls_get_the_widgets_the_page_was_drawn_for(const):
    # The number entity hands the register's own minimum, maximum and step to
    # the card (number.py), so this is what the Controls tab draws for each.
    registers = list(const.CONTROL_NUMBERS)
    kinds = _widgets(*((r.minimum, r.maximum, r.step) for r in registers))
    assert dict(zip((r.key for r in registers), kinds)) == {
        "ctl_max_rps": "slider",
        "ctl_immersion_lower": "slider",
        "ctl_immersion_upper": "slider",
        "ctl_room_setpoint_1": "field",
        "ctl_dhw_setpoint": "slider",
        "ctl_extra_dhw": "slider",
    }


def test_node_gets_the_helpers_and_nothing_is_left_as_a_global():
    # The guard hands over exactly the pure helpers and returns before the
    # custom elements, so require() never meets HTMLElement, and nothing lands
    # on the global object, where the NIBE card declares the same names.
    assert _run(
        "console.log(JSON.stringify({keys: Object.keys(c).sort(), steps: c.SLIDER_STEPS,"
        " leaked: [typeof globalThis.widgetFor, typeof globalThis.parseFieldValue,"
        " typeof globalThis.SLIDER_STEPS]}))"
    ) == {
        "keys": ["SLIDER_STEPS", "parseFieldValue", "widgetFor"],
        "steps": 130,
        "leaked": ["undefined"] * 3,
    }
