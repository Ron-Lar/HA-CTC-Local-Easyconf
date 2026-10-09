"""The words for states the page sends its cards (V1), checked without Home Assistant.

The cards word a binary state themselves, out of a table the page builds in
its own language, so a Swedish page never says "Not running". The table is
the TEXT entries that start with state_, handed to every card of values under
"states" (dashboard_views.state_words).
"""

from __future__ import annotations

import pytest

VALUE_CARDS = (
    "custom:ctc-ecozenith-chips",
    "custom:ctc-ecozenith-readings",
    "custom:ctc-ecozenith-controls",
    "custom:ctc-ecozenith-rows",
)


def _value_cards(config):
    for view in config["views"]:
        for section in view["sections"]:
            for card in section["cards"]:
                if card.get("type") in VALUE_CARDS:
                    yield card


def test_the_words_are_the_text_tables_state_entries_without_the_prefix(dashboard_views):
    assert dashboard_views.state_words(dashboard_views.TEXT["sv"]) == {
        "on": "Till", "off": "Av", "problem_on": "Larm", "problem_off": "OK",
    }
    assert dashboard_views.state_words(dashboard_views.TEXT["en"]) == {
        "on": "On", "off": "Off", "problem_on": "Alarm", "problem_off": "OK",
    }
    # Both languages have the same words to say, so a page in either reads whole.
    assert set(dashboard_views.state_words(dashboard_views.TEXT["sv"])) == set(
        dashboard_views.state_words(dashboard_views.TEXT["en"])
    )


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_every_card_of_values_carries_the_words_in_the_pages_language(dashboard_views, pumps, lang):
    for pump in pumps.values():
        pump["language"] = lang
        config = dashboard_views.build_dashboard([pump], "sv", (2026, 9))
        cards = list(_value_cards(config))
        assert cards
        words = dashboard_views.state_words(dashboard_views.TEXT[lang])
        assert all(card["states"] == words for card in cards)


def test_the_binaries_the_integration_makes_have_a_word_for_their_device_classes(dashboard_views):
    # The derived binaries are running, problem and none (binary_sensor.py);
    # running falls back on the plain pair, problem has its own.
    words = dashboard_views.state_words(dashboard_views.TEXT["sv"])
    assert words.get("running_on", words["on"]) == "Till"
    assert words.get("running_off", words["off"]) == "Av"
    assert (words["problem_on"], words["problem_off"]) == ("Larm", "OK")
