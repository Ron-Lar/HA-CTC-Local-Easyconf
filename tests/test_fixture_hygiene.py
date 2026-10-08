"""The public repository carries no address from the houses it was built in (R69).

The widget fixtures were captured over ssh from the two houses' Home Assistant
machines, and the capture used to end with the ssh session's own closing line,
which named the machine. Tests use the documentation range 192.0.2.0/24 instead,
the one RFC 5737 sets aside for examples, so nothing in here routes anywhere.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from conftest import FIXTURES

TESTS = pathlib.Path(__file__).resolve().parent

#: The houses' networks. Only this range: 192.168.1.x in the tests are made up
#: and belong to nobody in particular.
HOUSE_ADDRESS = re.compile(r"\b10\.0\.\d{1,3}\.\d{1,3}\b")

WIDGET_FIXTURES = sorted(FIXTURES.glob("widgets_*.json"))


def test_the_widget_fixtures_are_the_four_real_pages():
    assert [p.name for p in WIDGET_FIXTURES] == [
        "widgets_i255_118.json",
        "widgets_i255_128.json",
        "widgets_i550_136.json",
        "widgets_i550_138.json",
    ]


@pytest.mark.parametrize("path", WIDGET_FIXTURES, ids=lambda p: p.stem)
def test_a_widget_fixture_holds_the_screen_and_its_widgets_only(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"screen", "widgets"}, "the raw values were ssh noise and are gone"
    assert isinstance(data["screen"], int)
    assert data["widgets"] and all(isinstance(w["index"], int) for w in data["widgets"])


#: The line ssh prints when the capture's session ends, naming the machine.
SSH_NOISE = re.compile(r"Connection to \S+ closed")

#: Everything under tests/ that is text, except this file, which spells out
#: what it is looking for.
SCANNED = sorted(
    p for p in TESTS.rglob("*")
    if p.is_file() and p.suffix in (".py", ".json", ".txt", ".js") and p.name != pathlib.Path(__file__).name
)


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(TESTS)))
def test_nothing_under_tests_names_a_house(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    assert not HOUSE_ADDRESS.search(text), f"{path.name} carries an address from the houses"
    assert not SSH_NOISE.search(text), f"{path.name} carries the ssh session's closing line"
