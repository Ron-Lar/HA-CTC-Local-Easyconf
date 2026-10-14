"""The README says what follows Home Assistant's language and what does not (R23).

The page, its explanations and the integration's own entity names follow Home
Assistant's language; the display rows keep the panel's names, the states and
options keep the controller's Swedish words, and no entity id changes. The
README had said that Swedish was the choice, for the explanations as for the
names. Here its section on language is held to the decision, and its own words
for the entities to their English names: when a change of language shows and
whose language the forms follow (F1.2), that the page puts the line under a
figure in its own language though the attribute stays Swedish (F1.1), and that
the example of an action names the control as a new English installation has
it (F5.3).
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


def test_the_language_section_says_when_a_change_of_language_shows_and_whose_language_it_is():
    """F1.2: the page follows a new language when it is next opened, the names after a restart.

    Home Assistant reads a platform's translations when the platform is set
    up, so the entity names keep the old language until a restart or a reload
    of the entry, and the integration does not reload itself, since a reload
    moves the panel. The forms, the repairs and the actions are translated in
    the frontend in the user's own profile language, not the system's, so
    they do not follow "the same way".
    """
    section = _section("Language")
    assert "the page changes the next time it is opened" in section
    assert "the entity names at the next restart of Home Assistant or reload of the entry" in (
        section
    )
    assert "does not reload itself" in section
    assert (
        "The system language under *Settings, System, General* sets the page and the names"
        in section
    )
    assert (
        "the setup and options forms, the repairs view and the actions follow the language "
        "of each user's own profile"
    ) in section
    assert "the same way" not in section


def test_the_language_section_says_the_page_shows_the_line_under_a_figure_in_its_language():
    """F1.1: the attribute values stay Swedish, but the page puts its line in the page's words."""
    section = _section("Language")
    assert "Attribute values in text stay Swedish" in section
    assert "automations read the same words in every language" in section
    assert (
        "The CTC page shows the line under the figure, what it rests on or why it has none, "
        "in the page's language"
    ) in section


def test_the_release_example_names_the_control_as_a_new_english_installation_has_it():
    """F5.3: the example's entity id is a new English installation's, the Swedish one beside it."""
    actions = _section("Actions")
    assert "- number.ctc_ecozenith_i255_control_max_lower_immersion_heater" in actions
    assert "- number.ctc_ecozenith_i255_styr_max_elpatron_nedre" not in actions
    assert (
        "an installation from before the translations, or one in Swedish, has "
        "`number.ctc_ecozenith_i255_styr_max_elpatron_nedre` instead"
    ) in actions
    assert "put your own model in place of `i255`" in actions
    english = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["entity"]
    assert english["number"]["ctl_immersion_lower"]["name"] == "Control max lower immersion heater"


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
