"""Every explanation and source on the CTC page is written in English as well (R23).

The page is built in Home Assistant's language, Swedish where it is set to
Swedish and English otherwise, and asks explanations.py for its texts in that
language. Each text is a Text, the Swedish carrying its English, and Swedish is
the reserve: a text without English reads in Swedish rather than not at all.
Here, without Home Assistant: every text has its English, a text added in
Swedish alone, a plain string, fails until it gets one, the English names the
same numbers and registers as the Swedish and as the code, and an English page
shows English explanations and sources for every value, the display rows'
included, while a Swedish page shows what it always showed. That the names a
text gives of other entities are those entities' names in its language is
held in tests/test_entity_names.py, beside the names.
"""

from __future__ import annotations

import json
import re

import pytest

from conftest import load

NEW_HA = (2026, 9)
DASHES = ("–", "—", " - ")


def _composed(explanations) -> dict[str, object]:
    """Every text of the module by a name: the tables, the display rows and the parts."""
    texts: dict[str, object] = {}
    for table in ("MODBUS", "CONTROL", "DERIVED", "IDENTITY", "HARVEST"):
        texts.update({f"{table}:{key}": text for key, text in getattr(explanations, table).items()})
    texts.update({f"DISPLAY:{prefix}": text for prefix, text in explanations._DISPLAY})
    for part in (
        "_UNNAMED", "_GENERIC", "_COUNT_RESETS", "_VOLATILE", "_PUMP_VALUE",
        "_SHARE_WHAT", "_SHARE_EDGES", "_SHARE_SAMPLED",
    ):
        texts[part] = getattr(explanations, part)
    return texts


# ------------------------------------------------------- every text has both


def test_every_text_is_a_text_with_its_english(explanations):
    """A plain string or a text without English is a text added in Swedish alone."""
    missing = [
        name for name, text in _composed(explanations).items()
        if not isinstance(text, explanations.Text) or not text.en.strip()
    ]
    assert missing == []


def test_the_english_is_not_the_swedish_again(explanations):
    for name, text in _composed(explanations).items():
        assert text.en != str(text), name


def test_the_english_uses_no_dashes_as_punctuation(explanations):
    for name, text in _composed(explanations).items():
        for dash in DASHES:
            assert dash not in text.en, (name, dash)


def test_the_english_is_written_out_in_sentences(explanations):
    for name, text in _composed(explanations).items():
        english = text.en.strip()
        assert english[:1].isupper() or english[:1].isdigit(), name
        assert english.endswith("."), name


# ------------------------------------------------------------- the Text type


def test_two_texts_put_together_are_a_text_in_both_languages(explanations):
    Text = explanations.Text
    joined = Text("Hej.", "Hello.") + Text(" Då.", " Bye.")
    assert isinstance(joined, Text)
    assert str(joined) == "Hej. Då." and joined.en == "Hello. Bye."
    assert joined == "Hej. Då.", "a text is its Swedish"


def test_a_plain_string_put_to_a_text_leaves_it_without_english(explanations):
    Text = explanations.Text
    for joined in (Text("Hej.", "Hello.") + " Då.", "Då. " + Text("Hej.", "Hello.")):
        assert isinstance(joined, Text)
        assert joined.en == ""
        # On an English page it reads in Swedish rather than half and half.
        assert joined.in_language("en") == str(joined)


def test_swedish_is_the_reserve(explanations):
    Text = explanations.Text
    assert Text("Hej.", "Hello.").in_language("en") == "Hello."
    assert Text("Hej.", "Hello.").in_language("sv") == "Hej."
    assert Text("Hej.").in_language("en") == "Hej."
    assert explanations.in_language("Bara svenska.", "en") == "Bara svenska."
    assert type(Text("Hej.", "Hello.").in_language("sv")) is str


# ---------------------------------------------------------------- the lookups


def test_explain_gives_the_language_asked_for_and_swedish_by_default(explanations):
    for table in explanations.TABLES:
        for key, text in table.items():
            assert explanations.explain(key, "en") == text.en, key
            assert explanations.explain(key, "sv") == str(text), key
            assert explanations.explain(key) == str(text), key
    assert explanations.explain("nothing_like_it", "en") is None


def test_every_key_on_the_page_has_an_english_source(explanations, dashboard_views):
    for key in dashboard_views._KNOWN_KEYS:
        english = explanations.source(key, lang="en")
        swedish = explanations.source(key)
        assert english and swedish, key
        assert english != swedish, key
        assert "Modbus-register" not in english and "Räknas" not in english, key


def test_every_register_s_english_source_names_its_address(const, explanations):
    rows = (
        const.MODBUS_SENSORS + const.MODBUS_SETTINGS + const.CONTROL_NUMBERS + const.CONTROL_SELECTS
    )
    settings = {d.key for d in const.MODBUS_SETTINGS}
    controls = {r.key for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS}
    for row in rows:
        english = explanations.source(row.key, lang="en")
        assert english.startswith(f"Modbus register {row.address}"), row.key
        assert ("stored setting" in english) == (row.key in settings), row.key
        assert ("volatile" in english) == (row.key in controls), row.key


def test_a_display_row_s_source_in_english_names_its_page_screen_and_place(explanations):
    pages = [{"title": "Värmesystem", "values": [
        {"key": "p53_s61_v4", "label": "Status 2", "unit": None, "screen": 61, "row": 3},
        {"key": "p22_x", "label": "X", "unit": None},
    ]}]
    assert explanations.source("p53_s61_v4", pages, 1800, "en") == (
        "Display page Värmesystem, screen 61, row 3, read every 30 minutes"
    )
    assert explanations.source("p22_x", pages, None, "en") == "Display page Värmesystem"
    assert explanations.source("p22_x", pages, 1800) == (
        "Displaysidan Värmesystem, hämtas var 30:e minut"
    )


def test_a_display_row_is_explained_in_english_by_the_panel_s_swedish_label(explanations):
    explain = explanations.display_explanation
    assert explain("VP in/ut 2", "Driftinfo Värmepump", "en").startswith(
        "The water's temperature into and out of the heat pump."
    )
    assert explain("Avgiven värme totalt", "x", "en").startswith("All the heat")
    assert explain("Avgiven värme", "x", "en").startswith("The heating power")
    assert explain("Energy output total", "x", "en") == explain("Avgiven värme totalt", "x", "en")
    assert explain("Värde 4", "CTC EcoZenith i550 Pro", "en") == explanations._UNNAMED.en
    assert explain("Något helt okänt", "x", "en") == explanations._GENERIC.en
    assert explain("Något helt okänt", "x") == str(explanations._GENERIC)


# ------------------------------------------------------- the numbers in English


WORDS = {"one": 1, "two": 2, "three": 3, "five": 5, "ten": 10}


def _one(pattern: str, text: str) -> int:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    value = found.group(1)
    return int(value) if value.isdigit() else WORDS[value]


def _pair(pattern: str, text: str) -> tuple[int, int]:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    return int(found.group(1)), int(found.group(2))


def test_the_floors_and_ages_in_english_are_the_code_s(cop, const, explanations):
    def en(key):
        return explanations.explain(key, "en")

    assert _one(r"at least (\d+) kWh has been used", en("cop_day")) == cop.MIN_CONSUMPTION_KWH_DAY
    assert _one(r"at least (\d+) kWh has been used", en("cop_lifetime")) == cop.MIN_CONSUMPTION_KWH
    assert _one(r"at least (\d+) kWh has been used", en("cop_year")) == cop.MIN_CONSUMPTION_KWH
    assert _pair(r"(\d+) to (\d+) hours apart", en("cop_day")) == (
        cop.DAY_MIN_HOURS, cop.DAY_MAX_HOURS,
    )
    for span, total in (("week", "a week"), ("month", "a month")):
        days, tolerance = cop.WINDOWS[span]
        text = en(f"cop_{span}")
        assert _one(r"over the last (\d+) days", text) == days, span
        assert _pair(r"reading (\d+) to (\d+) days old", text) == (days, days + tolerance), span
        assert _one(r"at least (\d+) kWh per day", text) == cop.MIN_CONSUMPTION_KWH_DAY, span
        assert _one(rf"that is (\d+) kWh in {total}", text) == days * cop.MIN_CONSUMPTION_KWH_DAY
    assert _pair(r"(\d+) to (\d+) days old", en("cop_year")) == (
        const.COP_WINDOW_DAYS, cop.YEAR_MAX_DAYS,
    )


def test_the_share_s_floors_and_ages_in_english_are_the_code_s(cop, const, explanations):
    # The share of auxiliary heat (R28) waits on the coefficient of
    # performance's floors and ages, and says so in English as in Swedish.
    def en(key):
        return explanations.explain(key, "en")

    for span in cop.SHARE_SPANS:
        text = en(f"aux_share_{span}")
        assert text.startswith(explanations._SHARE_WHAT.en), span
        assert text.endswith(explanations._SHARE_EDGES.en), span
        assert (explanations._SHARE_SAMPLED.en in text) == (span in ("week", "month", "year"))
        assert _one(r"at least (\d+) hours \(register 62214\)", text) == cop.RUNNING_HOURS
    assert _one(r"at least (\d+) kWh has been used", en("aux_share_day")) == (
        cop.MIN_CONSUMPTION_KWH_DAY
    )
    assert _pair(r"(\d+) to (\d+) hours apart", en("aux_share_day")) == (
        cop.DAY_MIN_HOURS, cop.DAY_MAX_HOURS,
    )
    for span, total in (("week", "a week"), ("month", "a month")):
        days, tolerance = cop.WINDOWS[span]
        text = en(f"aux_share_{span}")
        assert _one(r"The last (\d+) days", text) == days, span
        assert _pair(r"reading (\d+) to (\d+) days old", text) == (days, days + tolerance), span
        assert _one(r"at least (\d+) kWh per day", text) == cop.MIN_CONSUMPTION_KWH_DAY, span
        assert _one(rf"that is (\d+) kWh over {total}", text) == days * cop.MIN_CONSUMPTION_KWH_DAY
    assert _pair(r"(\d+) to (\d+) days old", en("aux_share_year")) == (
        const.COP_WINDOW_DAYS, cop.YEAR_MAX_DAYS,
    )
    for span in ("year", "lifetime"):
        assert _one(r"at least (\d+) kWh has been used", en(f"aux_share_{span}")) == (
            cop.MIN_CONSUMPTION_KWH
        ), span


def test_the_intervals_and_counts_in_english_are_the_code_s(const, explanations):
    def en(key):
        return explanations.explain(key, "en")

    poll = _one(r"every (\d+) seconds by default", en("starts_today"))
    assert poll == const.DEFAULT_FAST_INTERVAL
    harvest = en("display_harvest")
    assert _one(r"every (\d+) minutes by default", harvest) * 60 == const.DEFAULT_SLOW_INTERVAL
    assert _one(r"tries again after (\w+) minutes", harvest) * 60 == const.RETRY_INTERVAL
    assert _one(r"at most (\w+) rounds? in a row", harvest) == const.HARVEST_SKIP_LIMIT
    room = en("ctl_room_setpoint_1")
    assert "every minute" in room and const.CONTROL_KEEPALIVE_SECONDS == 60
    assert _one(r"forgets about (\w+) minutes", room) * 60 == const.CONTROL_EXPIRY_SECONDS
    assert _one(r"for (\w+) minutes the control is released", room) * 60 == (
        const.CONTROL_EXPIRY_SECONDS
    )
    assert _one(r"within about (\w+) minutes", en("release_control")) * 60 == (
        const.CONTROL_EXPIRY_SECONDS
    )
    assert _one(r"the (\w+) latest alarms", en("alarm")) == const.ALARM_EPISODES
    for key in ("starts_today", "defrosts_today"):
        text = en(key)
        assert "Starts again from nought at midnight and whenever the entry is reloaded" in text
        assert "Home Assistant restarts" in text and "options are saved" in text
        assert "räknas sedan" in text, "the attribute by the name the entity carries"


def test_the_english_names_the_registers_the_swedish_names(explanations):
    for name, text in _composed(explanations).items():
        swedish = sorted(re.findall(r"\b(6[12]\d{3}|10\d\d)\b", str(text)))
        english = sorted(re.findall(r"\b(6[12]\d{3}|10\d\d)\b", text.en))
        assert english == swedish, name


# ------------------------------------------------------------------- the page


def _items(config: dict) -> list[dict]:
    found = []
    for view in config["views"]:
        for section in view["sections"]:
            for card in section["cards"][1:]:
                for item in card.get("items", card.get("rows", [])) or []:
                    if "entity" in item:
                        found.append(item)
    return found


def _english(pump: dict) -> dict:
    pump = json.loads(json.dumps(pump))
    pump["language"] = "en"
    return pump


@pytest.mark.parametrize("house", ["vsh", "pt"])
def test_an_english_page_explains_every_value_in_english(
    dashboard_views, explanations, pumps, house
):
    pump = _english(pumps[house])
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    rows = {
        value["key"]: (value, page)
        for page in pump["pages"] for value in page["values"]
    }
    config = dashboard_views.build_dashboard([pump], "en", NEW_HA)
    items = _items(config)
    assert items
    for item in items:
        key = by_entity[item["entity"]]
        if key in rows:
            value, page = rows[key]
            expected = explanations.display_explanation(value["label"], page["title"], "en")
        else:
            expected = explanations.explain(key, "en")
        assert item["explanation"] == expected, key
        assert item["source"] == explanations.source(
            key, pump["pages"], pump.get("display_interval"), "en"
        ), key
        assert "Modbus-register" not in item["source"], key


@pytest.mark.parametrize("house", ["vsh", "pt"])
def test_a_swedish_page_explains_every_value_as_it_always_did(
    dashboard_views, explanations, pumps, house
):
    pump = pumps[house]
    assert pump["language"] == "sv"
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    for item in _items(dashboard_views.build_dashboard([pump], "sv", NEW_HA)):
        key = by_entity[item["entity"]]
        if key.startswith("p") and explanations.explain(key) is None:
            continue
        assert item["explanation"] == explanations.explain(key), key


def test_a_value_the_page_does_not_know_is_explained_in_the_page_s_language(
    dashboard_views, pumps
):
    for lang in ("sv", "en"):
        pump = json.loads(json.dumps(pumps["vsh"]))
        pump["language"] = lang
        pump["entities"]["something_new"] = "sensor.ctc_ecozenith_i255_something_new"
        pump["names"]["something_new"] = "Something new"
        config = dashboard_views.build_dashboard([pump], lang, NEW_HA)
        (item,) = [i for i in _items(config) if i["entity"].endswith("_something_new")][:1]
        assert item["explanation"] == dashboard_views.TEXT[lang]["unknown_explanation"]
        assert item["source"] == dashboard_views.UNKNOWN_SOURCE
    swedish = dashboard_views.TEXT["sv"]["unknown_explanation"]
    assert swedish == dashboard_views.UNKNOWN_EXPLANATION


def test_the_page_s_words_carry_their_language(dashboard_views):
    assert set(dashboard_views.TEXT["sv"]) == set(dashboard_views.TEXT["en"])
    for lang in ("sv", "en"):
        assert dashboard_views.TEXT[lang]["language"] == lang


def test_an_english_section_card_explains_in_english(dashboard_views, explanations, pumps):
    section_card = load("section_card")
    pump = pumps["vsh"]
    for lang in ("en", "sv"):
        cards = section_card.section_cards(pump, "energy", NEW_HA, lang=lang)
        assert cards
        items = [row for card in cards for row in card.get("rows", []) if "entity" in row]
        assert items
        for item in items:
            key = next(k for k, e in pump["entities"].items() if e == item["entity"])
            if explanations.explain(key) is not None:
                assert item["explanation"] == explanations.explain(key, lang), (lang, key)
