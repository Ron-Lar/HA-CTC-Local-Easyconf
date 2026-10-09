"""The readings and rows cards' line under a value, in node against the stub DOM.

R35: an item with ``sub`` and ``reason`` names the entity attributes to draw
in small text under the figure: what it rests on, and, while the state is
unknown, why there is no figure. With ``show_reason`` the tile stays while the
state is merely unknown and goes only when the entity is unavailable; with
``hide_unavailable`` it goes for both, as before. The names are given as a
list, today's Swedish and the English they may get, and the first attribute
present is drawn, so a renaming in the integration does not blank the line.
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

COP = "sensor.ctc_cop_day"
OUTDOOR = "sensor.ctc_outdoor_temp"
NAMES = {"sub": ["underlag", "basis"], "reason": ["skäl", "reason"]}
ITEMS = [
    {"entity": COP, "name": "Dygnsvärmefaktor", "show_reason": True, **NAMES},
    {"entity": OUTDOOR, "name": "Utetemperatur", "hide_unavailable": True},
]
WAITING = "väntar på ett prov som är 20 till 30 timmar gammalt"


def _drawn(card: str, field: str, states: dict, then: dict | None = None) -> list[dict]:
    """Mount `card` over `states`, optionally send `then`, and read what it drew.

    Each tile or row comes back as whether it is hidden, the figure and the
    line under it (null where the item asked for none), in drawing order.
    """
    config = {field: ITEMS}
    cls = "tile" if card == "ctc-ecozenith-readings" else "row"
    program = (
        f"const dom = require({json.dumps(str(DOM))});\n"
        f"const t = dom.mountCard({json.dumps(str(CARD))}, {json.dumps(card)},"
        f" {json.dumps(config)}, {json.dumps(states)});\n"
        + (f"t.update({json.dumps(then)});\n" if then is not None else "")
        + f"const drawn = t.byClass({json.dumps(cls)}).map((e) => {{\n"
        "  const by = (name) => e.children.find((c) => c.className === name);\n"
        "  const sub = by('sub');\n"
        "  return { hidden: e.hidden, value: (by('big') || by('value')).textContent,"
        " sub: sub ? sub.textContent : null };\n"
        "});\n"
        "console.log(JSON.stringify(drawn));\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _state(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


@pytest.mark.parametrize("card, field", [
    ("ctc-ecozenith-readings", "items"), ("ctc-ecozenith-rows", "rows"),
])
def test_a_figure_shows_what_it_rests_on_and_an_empty_one_shows_why(card, field):
    with_figure = _drawn(card, field, {
        COP: _state("2.92", underlag="senaste dygnet", skäl=None),
        OUTDOOR: _state("7.5", unit_of_measurement="°C"),
    })
    assert with_figure == [
        {"hidden": False, "value": "2.92", "sub": "senaste dygnet"},
        {"hidden": False, "value": "7.5 °C", "sub": None},
    ]
    # Unknown: the coefficient stays, with the reason under it, while the
    # temperature, which has nothing to say, goes.
    empty = _drawn(card, field, {
        COP: _state("unknown", underlag="senaste dygnet", skäl=WAITING),
        OUTDOOR: _state("unknown"),
    })
    assert empty == [
        {"hidden": False, "value": "unknown", "sub": WAITING},
        {"hidden": True, "value": "unknown", "sub": None},
    ]


@pytest.mark.parametrize("card, field", [
    ("ctc-ecozenith-readings", "items"), ("ctc-ecozenith-rows", "rows"),
])
def test_only_an_unavailable_sensor_takes_the_coefficient_off_the_page(card, field):
    gone = _drawn(card, field, {COP: _state("unavailable"), OUTDOOR: _state("unavailable")})
    assert [d["hidden"] for d in gone] == [True, True]
    # And it comes back, line and all, when the sensor does.
    back = _drawn(
        card, field,
        {COP: _state("unavailable"), OUTDOOR: _state("unavailable")},
        then={COP: _state("unknown", underlag="senaste dygnet", skäl=WAITING), OUTDOOR: _state("7.5")},
    )
    assert back == [
        {"hidden": False, "value": "unknown", "sub": WAITING},
        {"hidden": False, "value": "7.5", "sub": None},
    ]


def test_the_english_attribute_names_are_found_too_and_nothing_is_drawn_without_them():
    renamed = _drawn("ctc-ecozenith-readings", "items", {
        COP: _state("unknown", basis="last day", reason="waiting for a sample"),
    })
    assert renamed[0] == {"hidden": False, "value": "unknown", "sub": "waiting for a sample"}
    # Unknown without a reason falls back on what it rests on; no attributes at
    # all leave the line empty, and the tile is drawn as before.
    assert _drawn("ctc-ecozenith-readings", "items", {COP: _state("unknown", underlag="senaste dygnet")})[0] == {
        "hidden": False, "value": "unknown", "sub": "senaste dygnet",
    }
    assert _drawn("ctc-ecozenith-readings", "items", {COP: _state("3.1")})[0] == {
        "hidden": False, "value": "3.1", "sub": "",
    }
