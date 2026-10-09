"""What the second look at phase 2 changed on the page (dashboard_views.py).

Checked without Home Assistant, like tests/test_dashboard.py. A graph is left
out whole without the line its heading promises (F6.1), the pump's day has no
SmartGrid band (F6.2), the word for a raised alarm is not the binary's own
name (F6.3), and a row whose unknown means that nothing has happened yet
carries the word for it (F6.5).
"""

from __future__ import annotations

NEW_HA = (2026, 9)

FLOW = "Framledning och börvärde det senaste dygnet"
COMPRESSOR = "Kompressorn det senaste dygnet"
TEMPERATURES = "Temperaturer det senaste dygnet"
PUMP = "Pumpen det senaste dygnet"


def _headings(sections):
    return [s["cards"][0]["heading"] for s in sections]


def _section(sections, heading):
    return next(s for s in sections if s["cards"][0]["heading"] == heading)


def _tab(dashboard_views, pump, tab, lang="sv"):
    build = getattr(dashboard_views, f"{tab}_sections")
    return build(pump, dashboard_views.TEXT[lang], NEW_HA)


def _graph_entities(section):
    (card,) = section["cards"][1:]
    return [e["entity"] if isinstance(e, dict) else e for e in card["entities"]]


# ------------------------------------------- F6.1: the line a heading promises


def test_the_flow_graph_goes_with_its_setpoint(dashboard_views, pumps):
    """An i255 whose heating circuit is switched off reads the setpoint as a
    flat 0 (VS1 on the i255 at home): without it the graph was two of the
    overview's five lines under a heading that promised a setpoint."""
    pump = pumps["vsh"]
    pump["unused"] = ["hs1_flow_setpoint", "degree_minutes"]
    headings = _headings(_tab(dashboard_views, pump, "performance"))
    assert FLOW not in headings
    assert headings[0] == COMPRESSOR
    # What it would have drawn is on the overview already.
    overview = _tab(dashboard_views, pump, "overview")
    drawn = set(_graph_entities(_section(overview, TEMPERATURES)))
    assert {pump["entities"]["hs1_flow"], pump["entities"]["outdoor_temp"]} <= drawn


def test_the_flow_graph_goes_without_a_setpoint_entity(dashboard_views, pumps):
    pump = pumps["pt"]
    del pump["entities"]["hs1_flow_setpoint"]
    assert FLOW not in _headings(_tab(dashboard_views, pump, "performance"))


def test_the_flow_graph_stays_with_its_setpoint_whatever_else_is_unused(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["unused"] = ["outdoor_temp"]
    section = _section(_tab(dashboard_views, pump, "performance"), FLOW)
    assert _graph_entities(section) == [
        pump["entities"]["hs1_flow"], pump["entities"]["hs1_flow_setpoint"],
    ]


def test_the_compressor_graph_goes_without_the_compressors_speed(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["unused"] = ["hp1_rps"]
    assert COMPRESSOR not in _headings(_tab(dashboard_views, pump, "performance"))
    # The degree minutes alone are not the compressor; the speed alone is.
    pump["unused"] = ["degree_minutes"]
    section = _section(_tab(dashboard_views, pump, "performance"), COMPRESSOR)
    assert _graph_entities(section) == [pump["entities"]["hp1_rps"]]


def test_every_line_a_heading_promises_is_one_of_its_graphs_keys(dashboard_views):
    graphs = {graph_id: keys for graph_id, _kind, keys in dashboard_views._GRAPHS}
    assert dashboard_views._GRAPH_REQUIRES == {
        "graph_flow": "hs1_flow_setpoint", "graph_compressor": "hp1_rps",
    }
    for graph_id, key in dashboard_views._GRAPH_REQUIRES.items():
        assert key in graphs[graph_id]


# ------------------------------------------------------ F6.2: no SmartGrid band


def test_the_pumps_day_has_no_smartgrid_band_on_either_pump(dashboard_views, pumps):
    """The mode's state is a label, "Normal" on a pump that never uses it, and
    a label is never zero: the rule for a reading only ever zero could not tell
    such a pump from one that does, and the band lay flat on both houses."""
    graphs = {graph_id: keys for graph_id, _kind, keys in dashboard_views._GRAPHS}
    assert "sg_mode" not in graphs["graph_pump"]
    for pump in pumps.values():
        sections = _tab(dashboard_views, pump, "overview")
        bands = _graph_entities(_section(sections, PUMP))
        assert pump["entities"]["sg_mode"] not in bands
        assert len(bands) == 3
        # The mode is still read, as a chip, and so is SmartGrid active.
        chips = {item["entity"] for item in _section(sections, pump["name"])["cards"][1]["items"]}
        assert {pump["entities"]["sg_mode"], pump["entities"]["smartgrid_active"]} <= chips


# --------------------------------------------- F6.3: the word for a raised alarm


def test_the_word_for_a_raised_alarm_is_not_the_binarys_name(dashboard_views, pumps):
    """The chip and the row write the name and then the word, and the binary
    is called Larm (binary_sensor.py), so "Larm" again read as "Larm Larm"."""
    for lang in ("sv", "en"):
        words = dashboard_views.state_words(dashboard_views.TEXT[lang])
        for pump in pumps.values():
            assert words["problem_on"].casefold() != pump["names"]["alarm"].casefold()
    assert dashboard_views.TEXT["sv"]["state_problem_on"] == "Utlöst"
    assert dashboard_views.TEXT["en"]["state_problem_on"] == "Raised"


# ------------------------------------ F6.5: a row whose unknown is nothing yet

NOTHING_YET = {
    "last_alarm": ("sensor.ctc_ecozenith_i255_senaste_larm", "Senaste larm"),
    "events": ("event.ctc_ecozenith_i255_handelser", "Händelser"),
    "last_start": ("sensor.ctc_ecozenith_i255_senaste_start", "Senaste start"),
    "last_run": ("sensor.ctc_ecozenith_i255_senaste_korning", "Senaste körning"),
    "last_defrost": ("sensor.ctc_ecozenith_i255_senaste_avfrostning", "Senaste avfrostning"),
}


def _with_nothing_yet(pump):
    """The fixture predates these entities; a running i255 has all five."""
    for key, (entity_id, name) in NOTHING_YET.items():
        pump["entities"][key] = entity_id
        pump["names"][key] = name
    return pump


def _every_item(config):
    for view in config["views"]:
        for section in view["sections"]:
            for card in section["cards"][1:]:
                for item in card.get("items", card.get("rows", [])):
                    if "entity" in item:
                        yield view["path"], item


def test_a_row_whose_unknown_means_nothing_yet_carries_the_word_for_it(dashboard_views, pumps):
    """The last alarm before a panel has shown one, the events before the first
    transition, the timestamps before a start or a defrost: "Okänd" in the full
    list for months, the look the list was rid of when the button left it."""
    pump = _with_nothing_yet(pumps["vsh"])
    by_entity = {entity_id: key for key, entity_id in pump["entities"].items()}
    for lang in ("sv", "en"):
        pump["language"] = lang
        config = dashboard_views.build_dashboard([pump], lang, NEW_HA)
        words = dashboard_views.state_words(dashboard_views.TEXT[lang])
        marked = {
            (path, by_entity[item["entity"]]): item["unknown_means"]
            for path, item in _every_item(config) if "unknown_means" in item
        }
        # In the full list, each of the five, and the word it points at is in
        # the table every card gets.
        assert {key: word for (path, key), word in marked.items() if path == "values"} == {
            "last_alarm": "none_alarm", "events": "none_event", "last_start": "none_start",
            "last_run": "none_run", "last_defrost": "none_defrost",
        }
        assert all(words[word] for word in marked.values())
        # Nothing else carries it, on any tab.
        assert {key for _path, key in marked} == set(NOTHING_YET)
        # The list still hides nothing: the row stays, with the word on it.
        rows = [item for path, item in _every_item(config) if path == "values"]
        assert all("hide_unavailable" not in item for item in rows)


def test_the_words_for_nothing_yet_read_beside_the_names(dashboard_views):
    sv = dashboard_views.state_words(dashboard_views.TEXT["sv"])
    en = dashboard_views.state_words(dashboard_views.TEXT["en"])
    assert [sv[w] for w in dashboard_views._NONE_YET.values()] == [
        "inget larm ännu", "ingen händelse ännu", "ingen start ännu",
        "ingen körning ännu", "ingen avfrostning ännu",
    ]
    assert [en[w] for w in dashboard_views._NONE_YET.values()] == [
        "no alarm yet", "no event yet", "no start yet", "no run yet", "no defrost yet",
    ]
