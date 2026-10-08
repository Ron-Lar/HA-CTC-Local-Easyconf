"""The report's keys are a closed list, and the consent text names them all.

The consent text under Configure once listed the coefficient of performance
over a day, a year and the lifetime and promised "never any single reading",
while the report had grown the first year, five flags about the counters and,
on a fault, the two lifetime totals in kilowatt hours. Nothing in the suite
noticed, because nothing compared the whole report with anything. These tests
do: every parameter of the builder is set, and the keys that come out have to
match the two closed lists in stats_extra exactly. A new key fails here first,
before it can leave the house unmentioned.
"""

from __future__ import annotations

import inspect
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"

#: Every parameter build_extra takes, each given a value that produces a key.
#: A parameter that is added to the builder has to be added here too, which is
#: the point: the test cannot be passed by leaving the new thing out.
EVERYTHING = {
    "model": "EcoZenith i255",
    "has_display": True,
    "control_enabled": True,
    "page_count": 7,
    "read_failures": 1,
    "heatpump_model": "EA720M",
    "serial": "720825400001",
    "display_firmware": "20260610",
    "heatpump_firmware": "20260522",
    "control_firmware": 610,
    "history_page": True,
    "heat_counter": True,
    "consumption_counter": False,
    "consumption_modbus": True,
    "heat_total": True,
    "consumption_total": True,
    "cop_floor": False,
    "cop_stuck": True,
    "cop_implausible": False,
    "heat_total_kwh": 22499.0,
    "consumption_total_kwh": 9116.0,
    "cop_day": 2.9,
    "cop_year": 2.6,
    "cop_first_year": 2.5,
    "cop_lifetime": 2.47,
}


def test_every_parameter_of_the_builder_is_set_here(stats_extra):
    parameters = set(inspect.signature(stats_extra.build_extra).parameters)
    assert parameters == set(EVERYTHING), (
        "build_extra har fått en ny parameter: lägg den i EVERYTHING, i FEATURE_KEYS "
        "eller METRIC_KEYS, i samtyckestexten, i README och på stats.rnet.se/integritet"
    )


def test_the_flags_and_the_numbers_are_closed_lists(stats_extra):
    payload = stats_extra.build_extra(**EVERYTHING)
    assert set(payload) == {"models", "features", "errors", "firmwares", "metrics"}
    assert set(payload["features"]) == stats_extra.FEATURE_KEYS
    assert set(payload["metrics"]) == stats_extra.METRIC_KEYS


def test_the_lists_hold_nothing_but_flags_and_numbers(stats_extra):
    payload = stats_extra.build_extra(**EVERYTHING)
    for key, value in payload["features"].items():
        assert isinstance(value, (bool, int)) and not isinstance(value, str), key
    for key, value in payload["metrics"].items():
        assert isinstance(value, (int, float)) and not isinstance(value, bool), key
    assert all(isinstance(v, str) for v in payload["firmwares"].values())
    # The models are slugs from a closed list, never the name as discovered.
    assert payload["models"] == ["i255", "ea720m"]


def test_the_lists_do_not_overlap_and_are_what_the_consent_text_counts(stats_extra):
    assert not (stats_extra.FEATURE_KEYS & stats_extra.METRIC_KEYS)
    # Four figures, two totals, three parts of the serial number's story.
    assert len(stats_extra.METRIC_KEYS) == 9
    # Four about the set-up, six about whether a figure is possible, three why not.
    assert len(stats_extra.FEATURE_KEYS) == 13


# ------------------------------------------------------ the consent text


def _consent(name: str) -> str:
    texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    return texts["options"]["step"]["init"]["data_description"]["send_statistics"]


def test_the_english_consent_text_names_what_the_report_carries():
    for name in ("strings.json", "translations/en.json"):
        text = _consent(name)
        for phrase in (
            "first year",
            "kilowatt hours",
            "only when it is wrong",
            "Python version",
            "devices",
            "hash",
            "warnings and errors",
            "{endpoint}",
            "{privacy_url}",
        ):
            assert phrase in text, f"{phrase!r} saknas i {name}"
        # The promise the report no longer kept.
        assert "any single reading" not in text, name


def test_the_swedish_consent_text_names_what_the_report_carries():
    text = _consent("translations/sv.json")
    for phrase in (
        "första år",
        "kilowattimmar",
        "bara när det är fel",
        "Python-version",
        "enheter",
        "hash",
        "varningar och fel",
        "{endpoint}",
        "{privacy_url}",
    ):
        assert phrase in text, f"{phrase!r} saknas i sv.json"
    assert "enskild avläsning" not in text
    # Swedish text in this repo is written without dashes as punctuation.
    assert "–" not in text and "—" not in text and " - " not in text


def test_the_two_english_texts_are_the_same():
    assert _consent("strings.json") == _consent("translations/en.json")


def test_the_readme_tells_the_same_story():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("## Anonymous statistics"):]
    section = section[: section.index("\n## ", 1)]
    for phrase in (
        "first year",
        "Python version",
        "devices",
        "hash",
        "warnings and errors",
        "FEATURE_KEYS",
        "METRIC_KEYS",
        "only when it is wrong",
    ):
        assert phrase in section, f"{phrase!r} saknas i README:s statistikavsnitt"


def test_the_draft_schema_that_said_nothing_was_implemented_is_gone():
    # docs/reporting.md was a public draft of a far larger report, with "nothing
    # here is implemented yet" at the top, next to a consent text describing
    # the real one. Two descriptions of what leaves the house is one too many.
    assert not (ROOT / "docs" / "reporting.md").exists()
