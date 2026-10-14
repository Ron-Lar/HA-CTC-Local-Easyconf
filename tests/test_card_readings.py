"""The readings and rows cards' line under a value, in node against the stub DOM.

R35: an item with ``sub`` and ``reason`` names the entity attributes to draw
in small text under the figure: what it rests on, and, while the state is
unknown, why there is no figure. With ``show_reason`` the tile stays while the
state is merely unknown and goes only when the entity is unavailable; with
``hide_unavailable`` it goes for both, as before. The names are given as a
list, today's Swedish and the English they may get, and the first attribute
present is drawn, so a renaming in the integration does not blank the line.

R23, R28 (F1.1, F5.1): the attributes' values stay Swedish for automations,
but the line is the page's own text, so an English page sends the cards a
table under ``story`` (dashboard_views.story_words) and the card draws the
line in the page's words: the six spans by TEXT's basis_ entries, the reasons
that are always the same words, and patterns for the reasons with a figure or
a date in them. Held here against what cop.py's own functions give, so a
reason cop.py words anew without a translation fails, and through the card
itself in node; a line the table does not know passes as it is. The tests of
the table itself need no node.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
DOM = TESTS / "card_dom.js"
COP_SOURCE = TESTS.parent / "custom_components" / "ctc_ecozenith" / "cop.py"

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

COP = "sensor.ctc_cop_day"
OUTDOOR = "sensor.ctc_outdoor_temp"
NAMES = {"sub": ["underlag", "basis"], "reason": ["skäl", "reason"]}
ITEMS = [
    {"entity": COP, "name": "Dygnsvärmefaktor", "show_reason": True, **NAMES},
    {"entity": OUTDOOR, "name": "Utetemperatur", "hide_unavailable": True},
]
WAITING = "väntar på ett prov som är 20 till 30 timmar gammalt"


def _drawn(card: str, field: str, states: dict, then: dict | None = None) -> list[dict]:
    """Mount `card` over `states`, optionally send `then`, and read what it drew.

    Each tile or row comes back as whether it is hidden, the figure and the
    line under it (null where the item asked for none), in drawing order.
    """
    config = {field: ITEMS}
    cls = "tile" if card == "ctc-ecozenith-readings" else "row"
    program = (
        f"const dom = require({json.dumps(str(DOM))});\n"
        f"const t = dom.mountCard({json.dumps(str(CARD))}, {json.dumps(card)},"
        f" {json.dumps(config)}, {json.dumps(states)});\n"
        + (f"t.update({json.dumps(then)});\n" if then is not None else "")
        + f"const drawn = t.byClass({json.dumps(cls)}).map((e) => {{\n"
        "  const by = (name) => e.children.find((c) => c.className === name);\n"
        "  const sub = by('sub');\n"
        "  return { hidden: e.hidden, value: (by('big') || by('value')).textContent,"
        " sub: sub ? sub.textContent : null };\n"
        "});\n"
        "console.log(JSON.stringify(drawn));\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _state(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


@needs_node
@pytest.mark.parametrize("card, field", [
    ("ctc-ecozenith-readings", "items"), ("ctc-ecozenith-rows", "rows"),
])
def test_a_figure_shows_what_it_rests_on_and_an_empty_one_shows_why(card, field):
    with_figure = _drawn(card, field, {
        COP: _state("2.92", underlag="senaste dygnet", skäl=None),
        OUTDOOR: _state("7.5", unit_of_measurement="°C"),
    })
    assert with_figure == [
        {"hidden": False, "value": "2.92", "sub": "senaste dygnet"},
        {"hidden": False, "value": "7.5 °C", "sub": None},
    ]
    # Unknown: the coefficient stays, with the reason under it, while the
    # temperature, which has nothing to say, goes.
    empty = _drawn(card, field, {
        COP: _state("unknown", underlag="senaste dygnet", skäl=WAITING),
        OUTDOOR: _state("unknown"),
    })
    assert empty == [
        {"hidden": False, "value": "unknown", "sub": WAITING},
        {"hidden": True, "value": "unknown", "sub": None},
    ]


@needs_node
@pytest.mark.parametrize("card, field", [
    ("ctc-ecozenith-readings", "items"), ("ctc-ecozenith-rows", "rows"),
])
def test_only_an_unavailable_sensor_takes_the_coefficient_off_the_page(card, field):
    gone = _drawn(card, field, {COP: _state("unavailable"), OUTDOOR: _state("unavailable")})
    assert [d["hidden"] for d in gone] == [True, True]
    # And it comes back, line and all, when the sensor does.
    back = _drawn(
        card, field,
        {COP: _state("unavailable"), OUTDOOR: _state("unavailable")},
        then={COP: _state("unknown", underlag="senaste dygnet", skäl=WAITING),
              OUTDOOR: _state("7.5")},
    )
    assert back == [
        {"hidden": False, "value": "unknown", "sub": WAITING},
        {"hidden": False, "value": "7.5", "sub": None},
    ]


@needs_node
def test_the_english_attribute_names_are_found_too_and_nothing_is_drawn_without_them():
    renamed = _drawn("ctc-ecozenith-readings", "items", {
        COP: _state("unknown", basis="last day", reason="waiting for a sample"),
    })
    assert renamed[0] == {"hidden": False, "value": "unknown", "sub": "waiting for a sample"}
    # Unknown without a reason falls back on what it rests on; no attributes at
    # all leave the line empty, and the tile is drawn as before.
    assert _drawn("ctc-ecozenith-readings", "items",
                  {COP: _state("unknown", underlag="senaste dygnet")})[0] == {
        "hidden": False, "value": "unknown", "sub": "senaste dygnet",
    }
    assert _drawn("ctc-ecozenith-readings", "items", {COP: _state("3.1")})[0] == {
        "hidden": False, "value": "3.1", "sub": "",
    }


# ------------------------------------------- the line in the page's language

#: Words that say a line is still Swedish. A row name the panel prints, which
#: stays the panel's on every page, and dates are none of them.
SWEDISH = frozenset({
    "av", "dygn", "samlade", "prov", "provet", "sparat", "räknarna", "räknaren",
    "räknare", "sedan", "inte", "har", "ännu", "är", "så", "och", "elpatronens",
    "displayen", "displayens", "registret", "kvoten", "lite", "energi", "inget",
    "väntar", "timmar", "styrenheten", "noll", "fast", "senaste", "rullande", "år",
    "första", "året", "hela", "livslängden",
})

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


class _Store:
    """The tracker's store, in memory."""

    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def _tracker(cop, *samples):
    """A tracker holding ``samples``, each (moment, delivered, consumed, immersion heater)."""
    tracker = cop.CopTracker(_Store())
    asyncio.run(tracker.async_load())
    for moment, out, consumed, immersion in samples:
        asyncio.run(tracker.async_record(out, consumed, now=moment, immersion=immersion))
    return tracker


def _said(story, line: str) -> str:
    """What the card makes of ``line`` with ``story``, in Python: a whole line, then a pattern."""
    if line in story["words"]:
        return story["words"][line]
    for pattern in story["patterns"]:
        found = re.search(pattern["match"], line)
        if found:
            said = pattern["text"]
            for number, caught in enumerate(found.groups(), start=1):
                said = said.replace(f"${number}", caught)
            return said
    return line


def _swedish_in(line: str) -> set[str]:
    return set(re.findall(r"[a-zåäö]+", line.casefold())) & SWEDISH


def _reasons_cop_gives(cop) -> list[str]:
    """Every reason cop.py's functions give for an empty figure, in the states that give them.

    Driven through the tracker, reason_for, cop_reason, share_reason and
    display_silence rather than read off the source, so a reason is what a
    sensor would write in skäl. Which state gives which words is not held
    here, only that each of them is said on an English page.
    """
    cop_result, share_result = cop.CopResult, cop.ShareResult
    day = timedelta(days=1)
    empty = _tracker(cop)
    progress = _tracker(cop, (NOW - 3 * day, 100.0, 40.0, 5.0))
    gap = _tracker(cop, (NOW - 20 * day, 100.0, 40.0, 5.0))
    week = _tracker(cop, (NOW - 7 * day, 100.0, 40.0, 5.0))
    unsampled = _tracker(cop, (NOW - 7 * day, 100.0, 40.0, None), (NOW - day, 100.0, 40.0, None))
    recent = _tracker(cop, (NOW - day, 100.0, 40.0, 5.0))
    results = [
        empty.result_first_year(),
        empty.result_day(10.0, 5.0, now=NOW),
        empty.result_year(10.0, 5.0, today=TODAY),
        empty.result_window(10.0, 5.0, "week", today=TODAY),
        empty.result_lifetime(10.0, 5.0, today=TODAY),
        progress.result_window(120.0, 45.0, "week", today=TODAY),
        progress.result_window(120.0, 45.0, "month", today=TODAY),
        gap.result_window(120.0, 45.0, "week", today=TODAY),
        gap.result_year(120.0, 45.0, today=TODAY),
        week.result_window(50.0, 40.0, "week", today=TODAY),
        week.result_window(100.0, 41.0, "week", today=TODAY),
        recent.result_day(100.0, 40.0, now=NOW),
        recent.result_day(160.0, 45.0, now=NOW),
    ]
    shares = [
        empty.share_day(5.0, 1.0, now=NOW),
        empty.share_window(5.0, 1.0, "month", today=TODAY),
        empty.share_year(5.0, 1.0, today=TODAY),
        empty.share_lifetime(-1.0, 1.0, today=TODAY),
        empty.share_lifetime(0.0, 0.0, today=TODAY, hours=100.0),
        empty.share_lifetime(0.0, 0.0, today=TODAY),
        empty.share_lifetime(2.0, 1.0, today=TODAY),
        progress.share_window(45.0, 6.0, "week", today=TODAY),
        gap.share_window(45.0, 6.0, "week", today=TODAY),
        week.share_window(30.0, 5.0, "week", today=TODAY),
        week.share_window(40.0, 5.0, "week", today=TODAY),
        week.share_window(45.0, 5.0, "week", today=TODAY),
        unsampled.share_window(45.0, 6.0, "week", today=TODAY),
        unsampled.share_day(45.0, 6.0, now=NOW),
        recent.share_day(40.0, 5.0, now=NOW),
        recent.share_day(41.0, 5.0, now=NOW),
        recent.share_day(30.0, 5.0, now=NOW),
    ]
    quiet = cop.display_silence(SimpleNamespace(last_update_success=False))
    dated = cop.display_silence(SimpleNamespace(last_update_success=False, last_harvest=NOW))
    stale = cop.display_silence(
        SimpleNamespace(
            last_update_success=True,
            last_read=lambda key: NOW - timedelta(hours=2),
            is_fresh=lambda key: False,
        ),
        SimpleNamespace(key="p22_avgiven_varme_totalt", label="Avgiven värme totalt"),
    )
    reasons = [result.reason for result in results + shares]
    reasons += [cop.reason_for(result, result.energy_out, result.energy_in) for result in results]
    reasons += [
        cop.share_reason(result, result.compressor, result.immersion) for result in shares
    ]
    reasons += [
        quiet, dated, stale,
        cop.reason_for(results[0], None, None, display=dated),
        cop.reason_for(cop_result(None, "year", 0), None, None, modbus_answered=False),
        cop.reason_for(cop_result(None, "first_year", 0), None, None),
        cop.reason_for(cop_result(None, "year", 0), None, None),
        cop.reason_for(cop_result(None, "lifetime", 0, 0.0, 500.0), 0.0, 500.0, 100.0),
        cop.reason_for(cop_result(None, "lifetime", 0, 1000.0, 50.0), 1000.0, 50.0),
        cop.reason_for(cop_result(None, "lifetime", 0, 5.0, 5.0), 5.0, 5.0),
        cop.cop_reason(None, "year", None, None, modbus_answered=False),
        cop.cop_reason(None, "first_year", None, None),
        cop.cop_reason(None, "day", None, 5.0),
        cop.cop_reason(None, "year", None, 5.0),
        cop.cop_reason(None, "day", 5.0, 1.0),
        cop.cop_reason(None, "week", 5.0, 1.0, days=7),
        cop.cop_reason(None, "day", 100.0, 5.0),
        cop.cop_reason(None, "lifetime", 0.0, 100.0, 100.0),
        cop.share_reason(share_result(None, "day", 0), None, None, consumption_answered=False),
        cop.share_reason(share_result(None, "day", 0), None, None),
        cop.share_reason(share_result(None, "day", 0), 10.0, None),
        cop.share_reason(share_result(None, "day", 0), 10.0, None, immersion_answered=False),
        cop.share_reason(share_result(None, "day", 0), 0.0, 5.0, compressor_hours=100.0),
        cop.share_reason(share_result(None, "year", 0), 10.0, 5.0),
        cop.share_reason(share_result(None, "year", 0), 10.0, 5.0, display=stale),
    ]
    return sorted({reason for reason in reasons if reason})


def _cop_strings() -> set[str]:
    """Every string constant in cop.py, the implicitly joined ones whole."""
    tree = ast.parse(COP_SOURCE.read_text(encoding="utf-8"))
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_a_swedish_page_sends_no_table_and_an_english_one_says_every_span_in_english(
    dashboard_views, cop
):
    sv, en = dashboard_views.TEXT["sv"], dashboard_views.TEXT["en"]
    assert dashboard_views.story_words(sv) is None
    story = dashboard_views.story_words(en)
    for span, swedish in cop.BASIS_WORDS.items():
        # TEXT's Swedish words are the attribute's own, and the English differ.
        assert sv[f"basis_{span}"] == swedish, span
        assert story["words"][swedish] == en[f"basis_{span}"], span
        assert not _swedish_in(en[f"basis_{span}"]), span
    assert en["basis_day"] == "last day" and en["basis_week"] == "last 7 days"
    # What the sensors write in underlag is among them, for every span.
    for span in (*cop.SHARE_SPANS, "first_year"):
        line = cop.CopResult(None, span, 0).as_attributes()["underlag"]
        assert _said(story, line) == en[f"basis_{span}"], span
    for span in cop.SHARE_SPANS:
        line = cop.ShareResult(None, span, 0).as_attributes()["underlag"]
        assert _said(story, line) == en[f"basis_{span}"], span


def test_every_reason_cops_functions_give_is_said_on_an_english_page(dashboard_views, cop):
    story = dashboard_views.story_words(dashboard_views.TEXT["en"])
    reasons = _reasons_cop_gives(cop)
    assert len(reasons) >= 20, reasons
    for reason in reasons:
        said = _said(story, reason)
        assert said != reason, reason
        assert not _swedish_in(said), (reason, said)
    # The fixed words, cop's constants and the literals beside them, are
    # whole lines in the table, and every whole line there is one cop.py
    # still writes, so a reworded reason leaves no dead translation behind.
    for name in dir(cop):
        value = getattr(cop, name)
        if name.startswith("SHARE_") and isinstance(value, str):
            assert value in story["words"], name
    written = _cop_strings()
    for swedish in story["words"]:
        assert swedish in written, swedish


@pytest.mark.parametrize("line, said", [
    ("3 av 7 dygn samlade, äldsta provet 2026-10-10",
     "3 of 7 days collected, oldest sample 2026-10-10"),
    ("2 av 30 dygn samlade, inget prov med elpatronens räknare sparat ännu",
     "2 of 30 days collected, no sample with the immersion heater's counter kept yet"),
    ("0 av 365 dygn samlade, inget prov sparat ännu",
     "0 of 365 days collected, no sample kept yet"),
    ("inget sparat prov är 7 till 9 dygn gammalt", "no kept sample is 7 to 9 days old"),
    ("väntar på ett prov som är 20 till 30 timmar gammalt",
     "waiting for a sample 20 to 30 hours old"),
    ("för lite energi ännu, 1.5 av 21 kWh", "too little energy so far, 1.5 of 21 kWh"),
    ("kvoten 12.0 är orimlig, räknarna hör inte ihop",
     "the quotient 12.0 is implausible; the counters do not belong together"),
    ("displayen har inte svarat sedan 2026-10-13T12:00:00+00:00",
     "the display has not answered since 2026-10-13T12:00:00+00:00"),
    ("displayens rad Avgiven värme totalt har inte lästs sedan 2026-10-13T10:00:00+00:00",
     "the display's row Avgiven värme totalt has not been read since 2026-10-13T10:00:00+00:00"),
    ("räknarna har gått bakåt sedan 2026-10-06, enheten är bytt eller nollställd",
     "the counters have gone backwards since 2026-10-06; the unit has been replaced or reset"),
    ("båda räknarna har stått still sedan 2026-10-12T12:00+00:00",
     "both counters have stood still since 2026-10-12T12:00+00:00"),
    ("kompressorns räknare står på noll fast kompressorn har gått 100 timmar, styrenheten "
     "fyller den inte",
     "the compressor's counter stands at zero although the compressor has run for 100 hours; "
     "the controller does not fill it"),
])
def test_a_reason_with_a_figure_in_it_is_said_with_the_figure(dashboard_views, line, said):
    """The patterns, the one for F2.1's samples without the immersion heater's counter too."""
    story = dashboard_views.story_words(dashboard_views.TEXT["en"])
    assert _said(story, line) == said


def _english_pump(pumps, language="en"):
    """VSH as a Home Assistant in ``language`` hands it over, with every span and share."""
    pump = pumps["vsh"]
    pump["language"] = language
    for span in ("week", "month"):
        pump["entities"][f"cop_{span}"] = f"sensor.ctc_ecozenith_i255_cop_{span}"
        pump["names"][f"cop_{span}"] = f"COP, {span}"
    for span in ("day", "week", "month", "year", "lifetime"):
        pump["entities"][f"aux_share_{span}"] = f"sensor.ctc_ecozenith_i255_aux_share_{span}"
        pump["names"][f"aux_share_{span}"] = f"Auxiliary heat share, {span}"
    return pump


def _cards(config):
    """Every card of the page's views, in order."""
    for view in config["views"]:
        for section in view.get("sections") or []:
            yield from section.get("cards") or []


def _with_line(card) -> list[dict]:
    """The items of a card that draw a line under their value."""
    return [item for item in card.get("items", card.get("rows", [])) if "sub" in item]


@pytest.mark.parametrize("language", ["en", "nl"])
def test_an_english_page_lets_no_basis_word_reach_the_card_untranslated(
    dashboard_views, cop, pumps, language
):
    """F1.1: every card that draws the line carries the table; Dutch gets the English page."""
    config = dashboard_views.build_dashboard([_english_pump(pumps, language)], "en", (2026, 9))
    english = {dashboard_views.TEXT["en"][f"basis_{span}"] for span in cop.BASIS_WORDS}
    drawing = [card for card in _cards(config) if _with_line(card)]
    assert len(drawing) >= 3  # the key figures, the energy section and the full list
    for card in drawing:
        for line in cop.BASIS_WORDS.values():
            said = _said(card["story"], line)
            assert said != line and said in english, line
    # Only a card that draws the line carries the table.
    assert all("story" not in card for card in _cards(config) if not _with_line(card))
    # A Swedish page draws the attributes' own words and sends nothing for them.
    swedish = dashboard_views.build_dashboard([_english_pump(pumps, "sv")], "sv", (2026, 9))
    assert all("story" not in card for card in _cards(swedish))


def _subs(card: str, config: dict, states: dict) -> list[str]:
    """Mount `card` with `config` over `states` and read every line under a value, in order."""
    program = (
        f"const dom = require({json.dumps(str(DOM))});\n"
        f"const t = dom.mountCard({json.dumps(str(CARD))}, {json.dumps(card)},"
        f" {json.dumps(config)}, {json.dumps(states)});\n"
        "console.log(JSON.stringify(t.byClass('sub').map((e) => e.textContent)));\n"
    )
    out = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _card_with(config, kind: str, entity: str) -> dict:
    """The first card of ``kind`` on the page that draws a line under ``entity``."""
    return next(
        card for card in _cards(config)
        if card["type"] == kind and any(i.get("entity") == entity for i in _with_line(card))
    )


def _lines(card: str, config: dict, states: dict) -> dict[str, str]:
    """The line under each value of a page's card, by entity."""
    entities = [item["entity"] for item in _with_line(config)]
    return dict(zip(entities, _subs(card, config, states), strict=True))


@needs_node
def test_an_english_page_draws_the_line_under_a_figure_in_english(dashboard_views, cop, pumps):
    """F5.1: last day and last 7 days under the figures, the share's reason in English, and
    a line the page does not know as it is."""
    pump = _english_pump(pumps)
    config = dashboard_views.build_dashboard([pump], "en", (2026, 9))
    ids = pump["entities"]
    unknown = "väntar på något som sidan inte känner till, 3 av 9"
    readings = _card_with(config, "custom:ctc-ecozenith-readings", ids["cop_day"])
    drawn = _lines("ctc-ecozenith-readings", readings, {
        ids["cop_day"]: _state(
            "2.92", underlag=cop.BASIS_WORDS["day"], basis=cop.BASIS_WORDS["day"]),
        ids["cop_week"]: _state(
            "3.05", underlag=cop.BASIS_WORDS["week"], basis=cop.BASIS_WORDS["week"]),
        ids["cop_month"]: _state(
            "unknown", underlag=cop.BASIS_WORDS["month"], skäl=unknown, reason=unknown),
    })
    assert drawn[ids["cop_day"]] == "last day"
    assert drawn[ids["cop_week"]] == "last 7 days"
    assert drawn[ids["cop_month"]] == unknown
    energy = _card_with(config, "custom:ctc-ecozenith-rows", ids["aux_share_week"])
    drawn = _lines("ctc-ecozenith-rows", energy, {
        ids["aux_share_week"]: _state(
            "unknown", underlag=cop.BASIS_WORDS["week"], basis=cop.BASIS_WORDS["week"],
            skäl=cop.SHARE_NOT_SAMPLED, reason=cop.SHARE_NOT_SAMPLED,
        ),
    })
    assert drawn[ids["aux_share_week"]] == energy["story"]["words"][cop.SHARE_NOT_SAMPLED]
    assert drawn[ids["aux_share_week"]].startswith("the samples in the basis lack")


@needs_node
def test_the_card_says_every_reason_as_the_table_does(dashboard_views, cop):
    """Every reason cop.py gives, through the card's own lookup, against the Python one above."""
    story = dashboard_views.story_words(dashboard_views.TEXT["en"])
    reasons = _reasons_cop_gives(cop)
    items = [
        {"entity": f"sensor.r{n}", "name": "R", "show_reason": True, **NAMES}
        for n in range(len(reasons))
    ]
    states = {f"sensor.r{n}": _state("unknown", skäl=reason) for n, reason in enumerate(reasons)}
    assert _subs("ctc-ecozenith-readings", {"items": items, "story": story}, states) == [
        _said(story, reason) for reason in reasons
    ]
    # Without the table, as from a Swedish or an older page, the line is the attribute's.
    assert _subs("ctc-ecozenith-readings", {"items": items}, states) == reasons
