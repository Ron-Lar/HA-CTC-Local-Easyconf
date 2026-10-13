"""All values on the page side (R39): the box for empty rows and a source line per row.

The i550's page Värmesystem has four rows that read "Status 2" and four that
read "Status 3", and every one of them had the same source line, "Displaysidan
Värmesystem", although the sensor knows its screen. The page now hands each
display value's screen and place in the page's list to the source line,
"Displaysidan Värmesystem, skärm 61, rad 3", so the rows can be told apart and
searched for. The list card gets the words for its box for empty rows and for
its count; no other list on the page gets the box.
"""

from __future__ import annotations

import copy

import pytest

NEW_HA = (2026, 9)


def test_the_source_line_names_the_screen_and_the_place_in_the_page(explanations):
    pages = [{"title": "Värmesystem", "values": [
        {"key": "p53_s61_v1", "label": "Status 2", "unit": None, "screen": 61, "row": 1},
        {"key": "p53_s61_v4", "label": "Status 2", "unit": None, "screen": 61, "row": 3},
    ]}]
    assert explanations.source("p53_s61_v4", pages, 1800) == (
        "Displaysidan Värmesystem, skärm 61, rad 3, hämtas var 30:e minut"
    )
    assert explanations.source("p53_s61_v1", pages, None) == (
        "Displaysidan Värmesystem, skärm 61, rad 1"
    )


def test_a_page_without_screen_and_place_reads_as_before(explanations):
    # What a page built from an older collection looks like, and the fixtures.
    pages = [{"title": "Driftinfo Värmepump",
              "values": [{"key": "p22_x", "label": "X", "unit": None}]}]
    assert explanations.source("p22_x", pages, 1800) == (
        "Displaysidan Driftinfo Värmepump, hämtas var 30:e minut"
    )


def _numbered(pump):
    """The fixture pump with each display value's screen and place, as dashboard.py sends them."""
    pump = copy.deepcopy(pump)
    for number, page in enumerate(pump["pages"]):
        for place, value in enumerate(page["values"], start=1):
            value["screen"] = 60 + number
            value["row"] = place
    return pump


def test_the_rows_that_share_a_name_get_source_lines_of_their_own(dashboard_views, pumps):
    pump = _numbered(pumps["pt"])
    pump["display_interval"] = 1800
    heating = next(p for p in pump["pages"] if p["title"] == "Värmesystem")
    # The i550's Värmesystem: four "Status 2" and four "Status 3". The fixture
    # has the page but not its entities, which a running i550 has.
    for value in heating["values"]:
        pump["entities"][value["key"]] = f"sensor.ctc_ecozenith_i550_pro_{value['key']}"
        pump["names"][value["key"]] = f"Värmesystem: {value['label']}"
    config = dashboard_views.build_dashboard([pump], "sv", NEW_HA)
    (view,) = [v for v in config["views"] if v["path"] == "values"]
    rows = [row for row in view["sections"][0]["cards"][1]["rows"] if "entity" in row]
    keys = {value["key"] for value in heating["values"]}
    by_entity = {e: k for k, e in pump["entities"].items()}
    listed = [row for row in rows if by_entity.get(row["entity"]) in keys]
    assert len(listed) == 8
    # Eight rows under four names twice over, and eight source lines.
    assert len({row["name"] for row in listed}) < len(listed)
    sources = [row["source"] for row in listed]
    assert len(set(sources)) == len(sources)
    assert sources[2] == "Displaysidan Värmesystem, skärm 64, rad 3, hämtas var 30:e minut"


@pytest.mark.parametrize("lang, box, count", [
    ("sv", "Visa även tomma", "visade / med värde / totalt"),
    ("en", "Show empty ones too", "shown / with a value / total"),
])
def test_the_list_of_all_values_gets_its_box_and_count_in_the_pages_language(
    dashboard_views, pumps, lang, box, count
):
    pump = copy.deepcopy(pumps["vsh"])
    pump["language"] = lang
    config = dashboard_views.build_dashboard([pump], lang, NEW_HA)
    (view,) = [v for v in config["views"] if v["path"] == "values"]
    card = view["sections"][0]["cards"][1]
    assert (card["show_empty"], card["count"]) == (box, count)
    # Only that list: the lists on the other tabs hide their empty rows by item.
    others = [
        card for v in config["views"] if v["path"] != "values"
        for section in v["sections"] for card in section["cards"]
        if card.get("type") == "custom:ctc-ecozenith-rows"
    ]
    assert others and not any("show_empty" in card for card in others)


def test_the_words_have_no_dashes_and_both_languages_have_them(dashboard_views):
    for lang in ("sv", "en"):
        for key in ("show_empty", "count"):
            word = dashboard_views.TEXT[lang][key]
            assert word and "—" not in word and " – " not in word and " - " not in word


def test_the_collection_sends_each_display_values_screen_and_place():
    # dashboard.py needs Home Assistant to run; the HA suite runs it for real
    # (test_homeassistant_list_source.py). Here, that the fields are sent.
    from conftest import COMPONENT

    collect = (COMPONENT / "dashboard.py").read_text(encoding="utf-8").split("def _collect")[1]
    collect = collect.split("\ndef ")[0]
    assert '"screen": v.screen, "row": place' in collect
    assert "for place, v in enumerate(page.values, start=1)" in collect
