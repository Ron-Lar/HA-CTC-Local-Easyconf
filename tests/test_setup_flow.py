"""The set-up flow as a stranger meets it, read without Home Assistant.

config_flow.py imports Home Assistant and voluptuous at the top, so the
ordinary suite reads it as source; test_homeassistant_config_flow.py drives the
same flow under a real core. What is pinned here holds in CI as well: the
first screen sweeps nothing (R70), and the texts that say what is switched on
from the start are there, in all three files, wherever a new installation
passes.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import COMPONENT

FLOW = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
FILES = ("strings.json", "translations/en.json", "translations/sv.json")


def _texts(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _method(name: str) -> str:
    """The source of one method of the flow, up to the next definition."""
    body = FLOW.split(f"async def {name}(")[1]
    return re.split(r"\n    (?:async )?def |\n    @", body)[0]


# ------------------------------------------------- nothing before a choice (R70)


def test_the_first_step_is_a_menu_that_sweeps_nothing():
    first = _method("async_step_user")
    assert "async_show_menu" in first
    assert "async_discover" not in first and "async_home_assistant_networks" not in first
    # The sweep lives in the step the menu's search leads to, and nowhere else.
    assert FLOW.count("await async_discover(") == 1
    assert "await async_discover(" in _method("async_step_scan")


@pytest.mark.parametrize("name", FILES)
def test_the_menu_offers_search_and_address(name):
    menu = _texts(name)["config"]["step"]["user"]["menu_options"]
    assert set(menu) == {"scan", "manual"}
    assert re.search(r'menu_options=\[STEP_SCAN, STEP_MANUAL\]', FLOW)
    assert 'STEP_SCAN = "scan"' in FLOW and 'STEP_MANUAL = "manual"' in FLOW


#: What is on from the start, in each language: control, the walk to System
#: information, the statistics and the release check, and where it is changed.
DEFAULTS = {
    "en": ("control", "System information", "statistics", "releases", "Configure", "{privacy_url}"),
    "sv": ("styrning", "Systeminformation", "statistiken", "versioner", "Konfigurera", "{privacy_url}"),
}


@pytest.mark.parametrize("name", FILES)
def test_every_way_in_says_what_is_on_from_the_start(name):
    """The menu for a flow somebody started, the confirmation for a discovered one,
    and the page step that most installations pass, all carry the same sentence."""
    language = "sv" if name.endswith("sv.json") else "en"
    steps = _texts(name)["config"]["step"]
    sentences = set()
    for step in ("user", "slow", "confirm"):
        text = steps[step]["description"]
        last = text.split("\n\n")[-1]
        for word in DEFAULTS[language]:
            assert word in last, f"{word!r} saknas i {step} i {name}"
        sentences.add(last)
    assert len(sentences) == 1, f"meningen ska vara densamma i alla tre stegen i {name}"


def test_the_privacy_page_travels_as_a_placeholder_to_every_step_that_names_it():
    # hassfest refuses a URL in strings.json, so the address must be handed in.
    assert "description_placeholders=STATS_PLACEHOLDERS" in _method("async_step_user")
    assert "**STATS_PLACEHOLDERS" in _method("async_step_confirm")
    slow = FLOW.split("async def async_step_slow(")[1].split("async def async_step_dhcp(")[0]
    assert "**STATS_PLACEHOLDERS" in slow


@pytest.mark.parametrize("name", FILES)
def test_the_address_form_no_longer_says_nothing_was_found(name):
    """The form is now a choice of its own; an empty search says so as an error."""
    config = _texts(name)["config"]
    manual = config["step"]["manual"]["description"]
    assert "No CTC was found" not in manual and "Ingen CTC hittades" not in manual
    assert config["error"]["nothing_found"]
    assert '{"base": "nothing_found"}' in FLOW


def test_the_swedish_flow_texts_use_no_dash_as_punctuation():
    def strings(node):
        if isinstance(node, dict):
            for value in node.values():
                yield from strings(value)
        elif isinstance(node, str):
            yield node

    for text in strings(_texts("translations/sv.json")["config"]):
        assert "–" not in text and "—" not in text and " - " not in text, text
