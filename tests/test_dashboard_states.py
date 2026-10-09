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
    sv = dashboard_views.state_words(dashboard_views.TEXT["sv"])
    en = dashboard_views.state_words(dashboard_views.TEXT["en"])
    assert {k: v for k, v in sv.items() if k != "unset_note"} == {
        "on": "Till", "off": "Av", "problem_on": "Utlöst", "problem_off": "OK",
        "unset": "ej satt", "no_limit": "ingen gräns",
    }
    assert {k: v for k, v in en.items() if k != "unset_note"} == {
        "on": "On", "off": "Off", "problem_on": "Raised", "problem_off": "OK",
        "unset": "not set", "no_limit": "no limit",
    }
    # The note the "i" beside "ej satt" opens: why there is no value.
    assert sv["unset_note"].startswith("Pumpen lämnar inte ut sitt eget värde här")
    assert en["unset_note"].startswith("The pump does not give out its own value here")
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


def _items(config):
    for card in _value_cards(config):
        for item in card.get("items", card.get("rows", [])):
            if "entity" in item:
                yield item


def test_only_the_compressors_top_speed_means_no_limit_by_zero(dashboard_views, pumps):
    # V2: the stored setting and the control that mirrors it, on every tab
    # they stand on, and nothing else: the immersion heater limits are an
    # open question and read as they are.
    pump = pumps["vsh"]
    # Neither house has the stored top speed switched on; here it is.
    pump["entities"]["set_max_rps_1"] = "sensor.ctc_ecozenith_i255_installt_max_varvtal"
    pump["names"]["set_max_rps_1"] = "Inställt max varvtal"
    config = dashboard_views.build_dashboard([pump], "sv", (2026, 9))
    by_entity = {e: k for k, e in pump["entities"].items()}
    marked = {by_entity[item["entity"]] for item in _items(config) if "zero_means" in item}
    assert marked == {"ctl_max_rps", "set_max_rps_1"}
    assert {item["zero_means"] for item in _items(config) if "zero_means" in item} == {"no_limit"}
    # The word it points at is in the table every card gets.
    assert all("no_limit" in card["states"] for card in _value_cards(config))


def test_the_binaries_the_integration_makes_have_a_word_for_their_device_classes(dashboard_views):
    # The derived binaries are running, problem and none (binary_sensor.py);
    # running falls back on the plain pair, problem has its own.
    words = dashboard_views.state_words(dashboard_views.TEXT["sv"])
    assert words.get("running_on", words["on"]) == "Till"
    assert words.get("running_off", words["off"]) == "Av"
    # The binary is called Larm, so the word for a raised one is another word.
    assert (words["problem_on"], words["problem_off"]) == ("Utlöst", "OK")
