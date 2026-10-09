"""The chips card, worked in node against the stub DOM (tests/card_dom.js).

R36: a boolean chip carries ``on_state`` and ``color`` from the page's YAML
(dashboard_views._CHIP_COLOURS). It is on the line only while the entity reads
its on state, so a quiet pump shows no "Larm: OK" and no "Avfrostning: Av", and
while it is there it bears the colour of what it means as a data attribute the
card's own style turns into the theme's colour. An enum chip has no on_state
and stays whatever it reads, an unavailable one included: an unavailable pump
is news.
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

ALARM = "binary_sensor.ctc_alarm"
RUNNING = "binary_sensor.ctc_compressor_running"
STATUS = "sensor.ctc_hp1_status"

ITEMS = [
    {"entity": STATUS, "name": "Värmepump status"},
    {"entity": RUNNING, "name": "Kompressor i drift", "on_state": "on", "color": "success"},
    {"entity": ALARM, "name": "Larm", "on_state": "on", "color": "error"},
]


def _chips(states: dict, then: dict | None = None, words: dict | None = None) -> list[dict]:
    """Mount the chips over `states`, optionally send `then`, and read the chips.

    Each chip comes back as its name, whether it is hidden, what it shows and
    the colour it was given, in drawing order. `words` is the page's table of
    state words (dashboard_views.state_words), sent under `states` when given.
    """
    config = {"items": ITEMS}
    if words is not None:
        config["states"] = words
    program = (
        f"const dom = require({json.dumps(str(DOM))});\n"
        f"const t = dom.mountCard({json.dumps(str(CARD))}, 'ctc-ecozenith-chips',"
        f" {json.dumps(config)}, {json.dumps(states)});\n"
        + (f"t.update({json.dumps(then)});\n" if then is not None else "")
        + "const chips = t.byClass('chip').map((chip) => ({"
        "  name: chip.children[0].textContent, hidden: chip.hidden,"
        "  value: chip.children[1].textContent, color: chip.dataset.color || null }));\n"
        "console.log(JSON.stringify(chips));\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _state(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


def test_a_quiet_pump_shows_its_status_and_no_boolean_chip():
    chips = _chips({
        STATUS: _state("Redo för start"),
        RUNNING: _state("off"),
        ALARM: _state("off"),
    })
    assert chips == [
        {"name": "Värmepump status", "hidden": False, "value": "Redo för start", "color": None},
        {"name": "Kompressor i drift", "hidden": True, "value": "off", "color": "success"},
        {"name": "Larm", "hidden": True, "value": "off", "color": "error"},
    ]


def test_a_boolean_chip_comes_and_goes_with_its_state_and_keeps_its_colour():
    on = {STATUS: _state("Till värme"), RUNNING: _state("on"), ALARM: _state("off")}
    chips = _chips(on)
    assert [(c["name"], c["hidden"]) for c in chips] == [
        ("Värmepump status", False), ("Kompressor i drift", False), ("Larm", True),
    ]
    # The alarm is raised: red on the line. The compressor stops: its chip goes.
    later = _chips(on, then={STATUS: _state("Av, larm"), RUNNING: _state("off"), ALARM: _state("on")})
    assert [(c["name"], c["hidden"], c["color"]) for c in later] == [
        ("Värmepump status", False, None),
        ("Kompressor i drift", True, "success"),
        ("Larm", False, "error"),
    ]


def test_an_unavailable_or_unknown_boolean_is_not_on_and_an_enum_chip_stays():
    # Not on is not on: nothing is coloured in while the integration is not
    # sure. The enum chip is there whatever it reads, since an unavailable
    # pump is exactly what the status line is for.
    chips = _chips({STATUS: _state("unavailable"), RUNNING: _state("unavailable")})
    assert [(c["name"], c["hidden"]) for c in chips] == [
        ("Värmepump status", False), ("Kompressor i drift", True), ("Larm", True),
    ]
    chips = _chips({STATUS: _state("unknown"), RUNNING: _state("unknown"), ALARM: _state("on")})
    assert [(c["name"], c["hidden"]) for c in chips] == [
        ("Värmepump status", False), ("Kompressor i drift", True), ("Larm", False),
    ]


#: The page's words as the card tests assume them after F7.3: the raised
#: alarm has a word of its own beside the name, not the name over again.
WORDS = {"on": "Till", "off": "Av", "problem_on": "Utlöst", "problem_off": "OK"}


def _raised(words: dict) -> dict:
    """The alarm chip while the alarm is raised, with the words given."""
    chips = _chips({
        STATUS: _state("Av, larm"),
        RUNNING: _state("off"),
        ALARM: _state("on", device_class="problem"),
    }, words=words)
    return chips[2]


def test_the_raised_alarm_chip_reads_its_name_and_the_pages_word_once_each():
    # F7.3: the one chip that must be read from the door. Its name is the
    # entity's, "Larm" (binary_sensor.py), and the value beside it is the
    # page's word for a raised alarm, so the red pill reads "Larm Utlöst".
    assert _raised(WORDS) == {"name": "Larm", "hidden": False, "value": "Utlöst", "color": "error"}
    # The word the page had before, the name itself, is not written twice.
    assert _raised(dict(WORDS, problem_on="Larm")) == {
        "name": "Larm", "hidden": False, "value": "", "color": "error",
    }


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_the_raised_alarm_chip_never_reads_the_name_twice_with_the_pages_own_words(
    dashboard_views, lang
):
    # The seam between the page and the card, with the words the page really
    # sends (dashboard_views.state_words): whatever the page's word for a
    # raised alarm is, the chip reads "Larm" once, followed by that word when
    # it is another word and by nothing when it is the name again.
    words = dashboard_views.state_words(dashboard_views.TEXT[lang])
    chip = _raised(words)
    assert (chip["name"], chip["hidden"], chip["color"]) == ("Larm", False, "error")
    assert chip["value"] != "Larm"
    expected = "" if words["problem_on"].lower() == "larm" else words["problem_on"]
    assert chip["value"] == expected
