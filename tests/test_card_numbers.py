"""Figures that keep their width, and a colour scheme as a safety net (roadmap R40).

The values in the dense list, the key figures and the reading beside a slider
used proportional figures, so a number moved sideways when 9.8 became 10.2,
and nothing in the cards said which colour scheme the browser should draw a
list's pop-up, a number field's arrows or the search field's clear button in.
Each card now mounts with tabular figures on every class a number stands in
(.value, .big, .reading, and the list's count) and with color-scheme: light
dark on its host, which Home Assistant's own theme normally decides from the
root. Nothing more: no dark class and no colours of the cards' own.

There is no browser here, so what is pinned is the stylesheet each card puts
in its shadow root, through the same mount a browser would do (card_page.js
over the stub DOM of card_dom.js), and that the elements the numbers are drawn
in carry those classes.
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

SLIDER = {"min": 0, "max": 120, "step": 1, "unit_of_measurement": "rps"}
STATES = {
    "sensor.a": {"state": "9.8", "attributes": {"unit_of_measurement": "°C"}},
    "number.a": {"state": "50", "attributes": SLIDER},
}
CARDS = {
    "ctc-ecozenith-chips": {"items": [{"entity": "sensor.a", "name": "A"}]},
    "ctc-ecozenith-readings": {"items": [{"entity": "sensor.a", "name": "A"}]},
    "ctc-ecozenith-controls": {"items": [{"entity": "number.a", "name": "A"}]},
    "ctc-ecozenith-rows": {
        "rows": [{"entity": "sensor.a", "name": "A"}], "filter": "Sök", "count": "x",
    },
}

#: Every class a number is drawn in.
NUMBER_CLASSES = {".value", ".big", ".reading", ".count"}


def _mounted() -> dict[str, dict]:
    """Each card's stylesheet and the classes of the elements it drew."""
    program = (
        f"const page = require({json.dumps(str(PAGE))});\n"
        f"const cards = {json.dumps(CARDS)};\n"
        "const out = {};\n"
        "for (const [name, config] of Object.entries(cards)) {\n"
        f"  const card = page.mountCard({json.dumps(str(CARD))}, name, config, "
        f"{json.dumps(STATES)});\n"
        "  const classes = page.all(card, (e) => Boolean(e.className)).map((e) => e.className);\n"
        "  out[name] = {style: page.styleOf(card), classes};\n"
        "}\n"
        "console.log(JSON.stringify(out));\n"
    )
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _rules(style: str) -> dict[str, str]:
    """Every selector in the stylesheet with the declarations of its rules, joined."""
    flat = re.sub(r"/\*.*?\*/", "", style, flags=re.S)
    found: dict[str, str] = {}
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", flat):
        for selector in selectors.split(","):
            key = " ".join(selector.split())
            found[key] = (found.get(key, "") + " " + " ".join(declarations.split())).strip()
    return found


@pytest.fixture(scope="module")
def mounted() -> dict[str, dict]:
    return _mounted()


def test_every_card_draws_its_numbers_in_tabular_figures(mounted):
    for name, card in mounted.items():
        rules = _rules(card["style"])
        for selector in NUMBER_CLASSES:
            assert "font-variant-numeric: tabular-nums" in rules.get(selector, ""), (
                name, selector,
            )


def test_every_card_sets_a_colour_scheme_on_its_host_and_no_more(mounted):
    for name, card in mounted.items():
        rules = _rules(card["style"])
        assert "color-scheme: light dark" in rules[":host"], name
        # The safety net and nothing else: no dark class, no scheme per element.
        declared = re.sub(r"/\*.*?\*/", "", card["style"], flags=re.S)
        assert declared.count("color-scheme") == 1, name
        assert ".dark" not in declared, name


def test_the_numbers_are_drawn_in_the_classes_that_carry_the_rule(mounted):
    assert "value" in mounted["ctc-ecozenith-rows"]["classes"]
    assert "count" in mounted["ctc-ecozenith-rows"]["classes"]
    assert "big" in mounted["ctc-ecozenith-readings"]["classes"]
    assert "reading" in mounted["ctc-ecozenith-controls"]["classes"]
