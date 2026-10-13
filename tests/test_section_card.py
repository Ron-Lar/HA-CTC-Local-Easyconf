"""One section of the CTC page for the section card (section_card.py, roadmap L11).

The section card draws what the integration answers for a heat pump and a
section, and the answer is cut out of the page: these tests hold it to being
exactly what the whole page, built as the sidebar builds it, has in that
section, for both houses' pumps and in both languages; to offering every
section of the page whose cards it can draw, so that a section another change
adds to the page cannot quietly go missing from the card; and to telling the
two hot water sections apart. Checked without Home Assistant, from the pumps
dashboard.py would hand over (tests/fixtures/pumps.json).
"""

from __future__ import annotations

import copy
import re

import pytest

from conftest import load

NEW_HA = (2026, 9)
TABS = ("overview", "controls", "performance", "values")


@pytest.fixture(scope="module")
def section_card():
    return load("section_card")


def _page_section(views, pump, section_card, section):
    """The section as the whole page has it, or None where the page leaves it out."""
    tab, heading_key = section_card.SECTIONS[section]
    text = views.TEXT[views.language(pump.get("language"))]
    heading = pump["name"] if heading_key is None else text[heading_key]
    config = views.build_dashboard([pump], "sv", NEW_HA)
    view = next(v for v in config["views"] if v["path"] == tab)
    found = [s for s in view["sections"] if s["cards"][0]["heading"] == heading]
    return found[0] if found else None


@pytest.mark.parametrize("house", ["vsh", "pt"])
@pytest.mark.parametrize("lang", ["sv", "en"])
def test_every_section_is_what_the_whole_page_has_there(
    dashboard_views, section_card, pumps, house, lang
):
    pump = pumps[house]
    pump["language"] = lang
    for section in section_card.SECTIONS:
        cards = section_card.section_cards(pump, section, NEW_HA)
        page = _page_section(dashboard_views, pump, section_card, section)
        if page is None:
            assert cards == [], f"{section}: sidan har ingen sådan sektion, kortet inte heller"
        else:
            assert cards == page["cards"][1:], section


def test_every_section_of_the_page_with_cards_of_its_own_has_an_id(
    dashboard_views, section_card, pumps
):
    # A section another change adds to the page, with the page's own cards in
    # it, has to be offered by the section card too, or a dashboard could not
    # show it. Graphs and notes are Home Assistant's cards and are not offered.
    for house in ("vsh", "pt"):
        pump = pumps[house]
        offered = [
            section_card.section_cards(pump, section, NEW_HA) for section in section_card.SECTIONS
        ]
        for tab in TABS:
            build = getattr(dashboard_views, f"{tab}_sections")
            for section in build(pump, dashboard_views.TEXT["sv"], NEW_HA):
                own = [c for c in section["cards"][1:] if c["type"] in section_card.CARD_TYPES]
                if own:
                    heading = section["cards"][0]["heading"]
                    assert own in offered, f"{house}: {tab}/{heading} saknar sektions-id"


def test_the_hot_water_is_two_sections_one_of_readings_and_one_of_controls(
    dashboard_views, section_card, pumps
):
    pump = pumps["vsh"]
    (readings,) = section_card.section_cards(pump, "hot_water", NEW_HA)
    (controls,) = section_card.section_cards(pump, "hot_water_controls", NEW_HA)
    assert readings["type"] == dashboard_views.ROWS_CARD
    assert controls["type"] == dashboard_views.CONTROLS_CARD
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    assert {by_entity[i["entity"]] for i in controls["items"]} == {
        "ctl_dhw_mode", "ctl_dhw_setpoint", "ctl_extra_dhw",
    }
    assert "dhw_temp" in {by_entity[r["entity"]] for r in readings["rows"]}


def test_each_section_is_drawn_by_the_card_the_page_uses_for_it(
    dashboard_views, section_card, pumps
):
    pump = pumps["vsh"]
    kinds = {
        section: [c["type"] for c in section_card.section_cards(pump, section, NEW_HA)]
        for section in ("status", "quick_controls", "key_readings", "energy", "values")
    }
    assert kinds == {
        "status": [dashboard_views.CHIPS_CARD],
        "quick_controls": [dashboard_views.CONTROLS_CARD],
        "key_readings": [dashboard_views.READINGS_CARD],
        "energy": [dashboard_views.ROWS_CARD],
        "values": [dashboard_views.ROWS_CARD],
    }
    # The full list keeps its search field, in the pump's language.
    (values,) = section_card.section_cards(pump, "values", NEW_HA)
    assert values["filter"] == dashboard_views.TEXT["sv"]["filter"]


def test_a_pump_without_control_has_no_control_sections(section_card, pumps):
    pump = pumps["pt"]
    assert not pump["control_enabled"]
    for section in ("quick_controls", "heating_controls", "hot_water_controls", "power_controls"):
        assert section_card.section_cards(pump, section, NEW_HA) == [], section
    # Its readings are there all the same.
    assert section_card.section_cards(pump, "temperatures", NEW_HA)


def test_a_pump_named_like_a_heading_still_finds_its_status_and_its_key_figures(
    dashboard_views, section_card, pumps
):
    pump = copy.deepcopy(pumps["vsh"])
    for name in ("Nyckeltal", "Snabbstyrning"):
        pump["name"] = name
        (status,) = section_card.section_cards(pump, "status", NEW_HA)
        (figures,) = section_card.section_cards(pump, "key_readings", NEW_HA)
        (quick,) = section_card.section_cards(pump, "quick_controls", NEW_HA)
        assert status["type"] == dashboard_views.CHIPS_CARD, name
        assert figures["type"] == dashboard_views.READINGS_CARD, name
        assert quick["type"] == dashboard_views.CONTROLS_CARD, name


def test_the_section_follows_the_pumps_language(dashboard_views, section_card, pumps):
    pump = pumps["vsh"]
    (sv,) = section_card.section_cards(pump, "values", NEW_HA)
    pump["language"] = "en"
    (en,) = section_card.section_cards(pump, "values", NEW_HA)
    assert sv["filter"] == "Sök bland värdena"
    assert en["filter"] == "Search the values"
    assert en["more_info"] == dashboard_views.TEXT["en"]["more_info"]


def test_an_unknown_section_is_refused(section_card, pumps):
    with pytest.raises(KeyError):
        section_card.section_cards(pumps["vsh"], "graph_cop", NEW_HA)


def test_the_labels_name_the_tab_and_the_heading_and_read_apart(section_card):
    for lang in ("sv", "en"):
        labels = [section_card.label(section, lang) for section in section_card.SECTIONS]
        assert len(set(labels)) == len(labels), lang
    assert section_card.label("hot_water", "sv") == "Prestanda: Varmvatten"
    assert section_card.label("hot_water_controls", "sv") == "Styrning: Varmvatten"
    assert section_card.label("status", "sv") == "Översikt: Drift"
    assert section_card.label("values", "sv") == "Alla värden"
    assert section_card.label("energy", "en") == "Performance: Energy and COP"


def test_the_swedish_labels_use_no_dash_as_a_separator(section_card):
    for section in section_card.SECTIONS:
        text = section_card.label(section, "sv")
        assert not re.search("\\s-\\s|[\N{EN DASH}\N{EM DASH}]", text), text
