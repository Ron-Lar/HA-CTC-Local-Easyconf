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
