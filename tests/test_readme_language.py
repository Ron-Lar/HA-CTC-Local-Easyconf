"""The README says what follows Home Assistant's language and what does not (R23).

The page, its explanations and the integration's own entity names follow Home
Assistant's language; the display rows keep the panel's names, the states and
options keep the controller's Swedish words, and no entity id changes. The
README had said that Swedish was the choice, for the explanations as for the
names. Here its section on language is held to the decision, and its own words
for the entities to their English names.
"""

from __future__ import annotations

import json

from conftest import COMPONENT, ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")


def _section(heading: str) -> str:
    assert f"\n## {heading}\n" in README, heading
    return " ".join(README.split(f"\n## {heading}\n")[1].split("\n## ")[0].split())


def test_the_language_section_says_what_follows_home_assistant():
    section = _section("Language")
    assert "Swedish when that is Swedish, English for every other language" in section
    for part in ("CTC page", "explanation", "names of the integration's own entities"):
        assert part in section, part


def test_the_language_section_says_what_stays_as_it_is():
    section = _section("Language")
    assert "The display rows are named by the panel." in section
    assert "reads the panel's menu and captions in Swedish" in section
    assert "Släpp styrningen" in section and "controller's Swedish words" in section
    assert "Entity ids never change." in section
    assert "sensor.ctc_ecozenith_i255_utetemperatur" in section
    assert "A name you gave an entity yourself is kept." in section


def test_the_readme_calls_the_entities_by_their_english_names():
    english = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["entity"]
    for platform, key in (
        ("sensor", "last_alarm"),
        ("sensor", "active_overrides"),
        ("sensor", "display_harvest"),
        ("sensor", "compressor_kwh"),
        ("binary_sensor", "alarm"),
        ("button", "release_control"),
        ("event", "events"),
    ):
        assert english[platform][key]["name"] in README, key
    assert "in Swedish, like the entity names" not in README
    assert "like the entity names and the explanations" not in README


def test_the_explanations_are_said_to_follow_the_language():
    page = _section("The CTC page")
    assert "written in Swedish and in English" in page
    assert "(#language)" in page
