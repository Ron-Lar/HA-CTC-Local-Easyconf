"""The cards' layout on a narrow screen (R37), read off the style they mount with.

The list's grid asked for tracks of at least 320 px, and the key figures' for
140 px. CSS lays such a track out even inside a narrower card, so on a 360 px
phone, where the card's content is about 300 px wide, the list stuck out to
the right and its right-aligned, no-wrap values were clipped. The fix is in
the grid rule itself, min(320px, 100%): a track never asks for more than the
card has. The search field above the list takes the whole line under 480 px.

There is no browser here, so what is pinned is the stylesheet each card puts
in its shadow root, through the same mount a browser would do: a bare minmax
with a pixel minimum must not come back, and the controls card gets no
breakpoint of its own until someone sees a problem there.
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

CARDS = {
    "ctc-ecozenith-chips": {"items": [{"entity": "sensor.a", "name": "A"}]},
    "ctc-ecozenith-readings": {"items": [{"entity": "sensor.a", "name": "A"}]},
    "ctc-ecozenith-controls": {"items": [{"entity": "number.a", "name": "A"}]},
    "ctc-ecozenith-rows": {"rows": [{"entity": "sensor.a", "name": "A"}], "filter": "Sök"},
}

#: A grid track that asks for a fixed width whatever the card has.
BARE_MINMAX = re.compile(r"minmax\(\s*\d+px\s*,")


def _styles() -> dict[str, str]:
    """The stylesheet each card mounts with, by element name."""
    program = (
        f"const page = require({json.dumps(str(PAGE))});\n"
        f"const cards = {json.dumps(CARDS)};\n"
        "const out = {};\n"
        "for (const [name, config] of Object.entries(cards)) {\n"
        f"  out[name] = page.styleOf(page.mountCard({json.dumps(str(CARD))}, name, config));\n"
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _rule(style: str, selector: str) -> str:
    """The declarations of the first rule for `selector`, on one line."""
    found = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", style)
    assert found, f"no rule for {selector}"
    return " ".join(found.group(1).split())


def test_the_list_and_the_key_figures_never_ask_for_a_track_wider_than_the_card():
    styles = _styles()
    rows = _rule(styles["ctc-ecozenith-rows"], ".grid")
    tiles = _rule(styles["ctc-ecozenith-readings"], ".tiles")
    assert "repeat(auto-fill, minmax(min(320px, 100%), 1fr))" in rows
    assert "repeat(auto-fill, minmax(min(140px, 100%), 1fr))" in tiles
    # Nowhere does a grid fall back to a fixed minimum again.
    for name, style in styles.items():
        assert not BARE_MINMAX.search(style), f"{name} asks for a fixed track"


def test_the_search_field_takes_the_whole_line_on_a_phone():
    style = _styles()["ctc-ecozenith-rows"]
    # On a wide screen the field shares the line with the count and stops at 420 px.
    field = _rule(style, ".toolbar input")
    assert "flex: 1;" in field and "max-width: 420px;" in field
    # The field may shrink below its content, or the count would push it out.
    assert "min-width: 0;" in field
    # Under 480 px it takes the whole line, with no ceiling, and the count wraps.
    # The block up to and including its last inner rule's brace.
    narrow = re.search(r"@media \(max-width: 480px\)\s*\{(.*?\})\s*\}", style, re.S)
    assert narrow, "no breakpoint for the search field"
    assert _rule(narrow.group(1), ".toolbar input") == "flex-basis: 100%; max-width: none;"
    assert "flex-wrap: wrap;" in _rule(style, ".toolbar")


def test_the_controls_card_has_no_breakpoint_of_its_own():
    # Its label already has min-width 0 and wraps; a breakpoint waits for a
    # problem someone has seen.
    style = _styles()["ctc-ecozenith-controls"]
    assert "@media" not in style
    assert "min-width: 0;" in _rule(style, ".control .label")
