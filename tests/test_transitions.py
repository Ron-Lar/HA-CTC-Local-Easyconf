"""The transition watch: starts, run length, defrosts and events (R27, R31, L9).

Driven by hand with samples of status codes, the way the Modbus coordinator
feeds it, without Home Assistant. The codes are the ones const.py pins to
their labels: 3, 5 and 33 are heating, cooling and hot water, 4 is a defrost,
7 is an alarm, 1 is ready to start. The event entity itself needs a real core
and is driven in test_homeassistant_transitions.py; its log and cursor are here.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from conftest import load

#: A summer afternoon in Sweden, aware, as dt_util.now() hands it over.
TZ = timezone(timedelta(hours=2))


def at(hour: int, minute: int = 0, day: int = 9) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


@pytest.fixture(scope="module")
def transitions():
    return load("transitions")


@pytest.fixture()
def watch(transitions):
    return transitions.TransitionWatch()


def feed(transitions, watch, *steps, **extra):
    """Feed (time, hp code) pairs and return the kinds of what came out, in order."""
    kinds = []
    for moment, code in steps:
        kinds.extend(
            t.kind for t in watch.observe(transitions.Sample(moment, hp_status=code, **extra))
        )
    return kinds


# ------------------------------------------------------------- the baseline


def test_the_first_sample_is_the_baseline_even_when_running(transitions, watch):
    assert feed(transitions, watch, (at(10), 3)) == []
    assert watch.running is True
    assert watch.last_start is None
    assert watch.starts_today == 0
    assert watch.counting_since == at(10)


def test_a_run_under_way_at_the_baseline_ends_without_a_length(transitions, watch):
    assert feed(transitions, watch, (at(10), 3), (at(10, 20), 1)) == ["kompressor_stopp"]
    assert watch.last_run.started is None
    assert watch.last_run.ended == at(10, 20)
    assert watch.last_run.minutes is None


# --------------------------------------------------------- starts and stops


def test_a_start_and_a_stop_count_and_time_the_run(transitions, watch):
    kinds = feed(
        transitions, watch,
        (at(9), 1), (at(10), 3), (at(10, 45), 1),
        system_status=5, outdoor=7.5,
    )
    assert kinds == ["kompressor_start", "kompressor_stopp"]
    assert watch.last_start == at(10)
    assert watch.last_start_system_status == 5
    assert watch.last_start_outdoor == 7.5
    assert watch.starts_today == 1
    assert watch.running is False
    assert watch.last_run.started == at(10)
    assert watch.last_run.ended == at(10, 45)
    assert watch.last_run.minutes == 45.0


def test_heating_hot_water_and_cooling_are_one_run(transitions, watch):
    kinds = feed(
        transitions, watch, (at(9), 1), (at(10), 3), (at(10, 30), 33), (at(11), 5), (at(12), 0)
    )
    assert kinds == ["kompressor_start", "kompressor_stopp"]
    assert watch.starts_today == 1
    assert watch.last_run.minutes == 120.0


def test_a_defrost_in_the_middle_of_a_run_is_neither_a_stop_nor_a_start(transitions, watch):
    kinds = feed(
        transitions, watch, (at(9), 1), (at(10), 3), (at(10, 40), 4), (at(10, 48), 3), (at(11), 1)
    )
    assert kinds == ["kompressor_start", "avfrostning_start", "avfrostning_slut", "kompressor_stopp"]
    assert watch.starts_today == 1
    assert watch.last_run.minutes == 60.0


def test_the_transitions_say_what_they_went_from_and_to(transitions, watch):
    feed(transitions, watch, (at(9), 1))
    (start,) = watch.observe(transitions.Sample(at(10), hp_status=33, outdoor=-3.5))
    assert start.kind == "kompressor_start"
    assert start.at == at(10)
    assert (start.from_code, start.to_code) == (1, 33)
    assert (start.from_label, start.to_label) == ("Redo för start", "Till varmvatten")
    assert start.outdoor == -3.5
    assert start.minutes is None
    assert start.attributes() == {
        "från": "Redo för start", "till": "Till varmvatten",
        "från kod": 1, "till kod": 33, "utetemperatur": -3.5,
    }
    (stop,) = watch.observe(transitions.Sample(at(10, 12), hp_status=6, outdoor=-3.0))
    assert stop.kind == "kompressor_stopp"
    assert stop.minutes == 12.0
    assert stop.attributes()["längd"] == 12.0
    assert stop.attributes()["till"] == "Av, blockerad"


# ------------------------------------------------ codes that say nothing


@pytest.mark.parametrize("silent", [None, 8, 30, 31, 32, 99])
def test_a_code_that_says_nothing_holds_the_state(transitions, watch, silent):
    # A communication error that comes and goes must not be counted as starts.
    kinds = feed(
        transitions, watch,
        (at(9), 1), (at(10), 3), (at(10, 5), silent), (at(10, 10), 3), (at(10, 15), silent),
        (at(10, 20), 3),
    )
    assert kinds == ["kompressor_start"]
    assert watch.starts_today == 1
    assert watch.running is True
    # The stop is seen at the first known code after the silence, and the run
    # is measured from the start that was seen.
    assert feed(transitions, watch, (at(10, 25), silent), (at(10, 30), 1)) == ["kompressor_stopp"]
    assert watch.last_run.minutes == 30.0


def test_a_silent_baseline_is_no_baseline(transitions, watch):
    # Only the first known code sets the baseline, so a register that answered
    # nothing at start cannot make the first real reading look like a change.
    assert feed(transitions, watch, (at(9), None), (at(9, 1), 32), (at(9, 2), 3)) == []
    assert watch.running is True


def test_the_known_codes_are_the_table_without_the_silent_ones(transitions, const):
    known = {code for code in const.STATUS_HEATPUMP if transitions.hp_code_known(code)}
    assert known == set(const.STATUS_HEATPUMP) - transitions.HP_SILENT_CODES
    assert transitions.HP_SILENT_CODES == {8, 30, 31, 32}
    assert {const.STATUS_HEATPUMP[code] for code in transitions.HP_SILENT_CODES} == {
        "Funktionstest", "Ej definierad", "Ej tillgänglig", "Kommunikationsfel",
    }
    assert transitions.HP_RUN_CODES == const.HP_RUNNING_CODES | {const.HP_DEFROST_CODE}
    assert not transitions.hp_code_known(None)
    assert not transitions.hp_code_known(99)


# ---------------------------------------------------------------- midnight


def test_the_counts_start_over_at_midnight_local_time(transitions, watch):
    feed(transitions, watch, (at(22), 1), (at(22, 30), 3), (at(23), 1), (at(23, 30), 3))
    assert watch.starts_today == 2
    assert watch.counting_since == at(22)
    # The first sample of the new day: the count is the new day's, from midnight.
    assert feed(transitions, watch, (at(0, 0, day=10), 3)) == []
    assert watch.starts_today == 0
    assert watch.counting_since == datetime(2026, 10, 10, 0, 0, tzinfo=TZ)
    feed(transitions, watch, (at(0, 20, day=10), 1), (at(0, 40, day=10), 3))
    assert watch.starts_today == 1
    # The run that straddled midnight is still one run.
    assert watch.last_run.started == at(23, 30)
    assert watch.last_run.minutes == 50.0


# ---------------------------------------------------------------- defrosts


def test_a_defrost_is_counted_timed_and_dated_with_the_weather(transitions, watch):
    feed(transitions, watch, (at(9), 1), (at(10), 3))
    (start,) = watch.observe(transitions.Sample(at(10, 40), hp_status=4, outdoor=-2.5))
    assert start.kind == "avfrostning_start"
    assert start.attributes()["utetemperatur"] == -2.5
    assert watch.defrosting is True
    assert watch.defrosts_today == 1
    assert watch.last_defrost.started == at(10, 40)
    assert watch.last_defrost.outdoor == -2.5
    assert watch.last_defrost.ended is None and watch.last_defrost.minutes is None
    (end,) = watch.observe(transitions.Sample(at(10, 48), hp_status=3, outdoor=-2.0))
    assert end.kind == "avfrostning_slut"
    assert end.minutes == 8.0
    assert end.attributes()["längd"] == 8.0
    assert watch.defrosting is False
    assert watch.last_defrost.ended == at(10, 48)
    assert watch.last_defrost.minutes == 8.0
    # Still one start, still running: a defrost is no stop.
    assert watch.starts_today == 1
    assert watch.running is True


def test_a_defrost_that_ends_in_a_stop_ends_before_the_run_does(transitions, watch):
    feed(transitions, watch, (at(9), 1), (at(10), 3), (at(10, 40), 4))
    assert feed(transitions, watch, (at(10, 50), 1)) == ["avfrostning_slut", "kompressor_stopp"]
    assert watch.last_defrost.minutes == 10.0
    assert watch.last_run.minutes == 50.0


def test_a_defrost_straight_out_of_standstill_is_also_a_start(transitions, watch):
    # Unlikely, but the compressor turns in a defrost, so it is a run.
    assert feed(transitions, watch, (at(9), 1), (at(10), 4)) == [
        "kompressor_start", "avfrostning_start",
    ]
    assert watch.starts_today == 1 and watch.defrosts_today == 1


def test_a_defrost_under_way_at_the_baseline_ends_without_a_record(transitions, watch):
    assert feed(transitions, watch, (at(9), 4), (at(9, 5), 3)) == ["avfrostning_slut"]
    assert watch.last_defrost is None
    assert watch.defrosts_today == 0


def test_the_defrosts_start_over_at_midnight_too(transitions, watch):
    feed(transitions, watch, (at(23), 3), (at(23, 30), 4), (at(23, 40), 3))
    assert watch.defrosts_today == 1
    feed(transitions, watch, (at(0, 10, day=10), 3))
    assert watch.defrosts_today == 0
    assert watch.last_defrost.started == at(23, 30)


def test_the_defrost_sensors_read_the_watch(transitions, watch):
    defrosts = _sensor(transitions, "defrosts_today")
    last = _sensor(transitions, "last_defrost")
    assert defrosts.kind == "count" and last.kind == "timestamp"
    feed(transitions, watch, (at(9), 3))
    assert defrosts.value(watch) == 0
    assert defrosts.attributes(watch) == {"räknas sedan": "2026-10-09T09:00:00+02:00"}
    assert last.value(watch) is None
    assert last.attributes(watch) == {
        "längd": None, "utetemperatur vid starten": None, "avslutad": None, "pågår": False,
    }
    feed(transitions, watch, (at(9, 30), 4), outdoor=-4.0)
    assert defrosts.value(watch) == 1
    assert last.value(watch) == at(9, 30)
    assert last.attributes(watch) == {
        "längd": None, "utetemperatur vid starten": -4.0, "avslutad": None, "pågår": True,
    }
    feed(transitions, watch, (at(9, 36), 3))
    assert last.attributes(watch) == {
        "längd": 6.0, "utetemperatur vid starten": -4.0,
        "avslutad": "2026-10-09T09:36:00+02:00", "pågår": False,
    }


def test_the_defrost_words_say_what_is_unconfirmed_and_what_goes_negative(explanations):
    # The timer's unit is unknown: the register list names none and the panel
    # prints the number bare. And delivered heat is expected below zero while
    # the circuit runs backwards to thaw the evaporator.
    assert "obekräftad" in explanations.explain("hp1_defrost_timer")
    assert "obekräftad" in explanations.display_explanation("Timer avfrostning", "x")
    for label in ("Avgiven värme", "Energy output"):
        text = explanations.display_explanation(label, "x")
        assert "negativt" in text and "avfrostning" in text, label
    # The totals and periods keep their own words.
    assert "negativt" not in explanations.display_explanation("Avgiven värme totalt", "x")
    assert "negativt" not in explanations.display_explanation("Avgiven värme/30 dagar", "x")
    assert "stopp" in explanations.explain("last_defrost")


# ------------------------------------------------------------------ alarms


def test_the_alarm_comes_and_goes(transitions, watch):
    assert feed(transitions, watch, (at(9), 3), (at(9, 10), 7)) == ["kompressor_stopp", "larm"]
    assert watch.running is False
    # Back into a run straight out of the alarm: the alarm goes first, then the start.
    assert feed(transitions, watch, (at(9, 30), 3)) == ["larm_borta", "kompressor_start"]
    assert watch.starts_today == 1
    # An alarm that ends in a stopped pump is only the alarm going.
    assert feed(transitions, watch, (at(10), 7), (at(10, 5), 1)) == [
        "kompressor_stopp", "larm", "larm_borta",
    ]


# ---------------------------------------- system status and SmartGrid


def test_system_status_and_smartgrid_changes_carry_codes_and_labels(transitions, watch):
    first = watch.observe(transitions.Sample(at(9), hp_status=1, system_status=5, sg_mode=0))
    assert first == []
    found = watch.observe(
        transitions.Sample(at(9, 1), hp_status=1, system_status=4, sg_mode=2, outdoor=1.0)
    )
    assert [t.kind for t in found] == ["systemstatus_andrad", "smartgrid_andrad"]
    system, grid = found
    assert (system.from_code, system.to_code) == (5, 4)
    assert (system.from_label, system.to_label) == ("Varmvatten", "Värme")
    assert (grid.from_label, grid.to_label) == ("Normal", "Lågpris")
    assert grid.attributes()["utetemperatur"] == 1.0
    # A code the table has no label for is still a change, and says its number.
    (odd,) = watch.observe(transitions.Sample(at(9, 2), hp_status=1, system_status=12, sg_mode=2))
    assert odd.kind == "systemstatus_andrad"
    assert odd.to_label == "Okänd (12)"
    # A register that did not answer changes nothing, and the next answer is
    # compared with the last one that did.
    assert watch.observe(transitions.Sample(at(9, 3), hp_status=1)) == []
    assert watch.observe(transitions.Sample(at(9, 4), hp_status=1, system_status=12, sg_mode=2)) == []


# ------------------------------------------------- the log and its cursor (L9)


def test_the_log_numbers_the_transitions_and_a_cursor_reads_what_came_after(transitions, watch):
    assert watch.seq == 0 and watch.since(0) == []
    feed(transitions, watch, (at(9), 1), (at(10), 3), (at(10, 30), 4))
    found = watch.since(0)
    assert [t.kind for t in found] == ["kompressor_start", "avfrostning_start"]
    assert [t.seq for t in found] == [1, 2]
    assert watch.seq == 2
    # A reader that has fired up to 2 is handed nothing until something new.
    assert watch.since(2) == []
    feed(transitions, watch, (at(10, 38), 7))
    later = watch.since(2)
    assert [t.kind for t in later] == ["avfrostning_slut", "kompressor_stopp", "larm"]
    assert [t.seq for t in later] == [3, 4, 5]
    # What observe() handed back is what the log holds, the same objects.
    assert list(watch.log)[-3:] == later


def test_the_log_is_bounded_and_a_reader_that_starts_late_gets_nothing_old(transitions):
    short = transitions.TransitionWatch(keep=3)
    feed(transitions, short, (at(9), 1), (at(9, 10), 3), (at(9, 20), 1), (at(9, 30), 3), (at(9, 40), 1))
    assert short.seq == 4
    assert [t.seq for t in short.since(0)] == [2, 3, 4]
    # The event entity starts its cursor at the watch's number when it is made.
    assert short.since(short.seq) == []
    assert transitions.LOG_LENGTH >= 20


def test_every_kind_the_watch_can_raise_is_an_event_type_and_the_list_is_the_roadmaps(
    transitions, watch
):
    raised = set()
    for moment, hp, system, sg in (
        (at(9), 1, 0, 0),
        (at(9, 10), 3, 0, 0),    # start
        (at(9, 20), 4, 5, 2),    # defrost start, system status, smartgrid
        (at(9, 30), 3, 5, 2),    # defrost end
        (at(9, 40), 7, 5, 2),    # stop and alarm
        (at(9, 50), 1, 5, 2),    # alarm cleared
    ):
        raised |= {
            t.kind
            for t in watch.observe(
                transitions.Sample(moment, hp_status=hp, system_status=system, sg_mode=sg)
            )
        }
    assert raised == set(transitions.EVENT_TYPES)
    assert transitions.EVENT_TYPES == (
        "kompressor_start", "kompressor_stopp", "avfrostning_start", "avfrostning_slut",
        "larm", "larm_borta", "smartgrid_andrad", "systemstatus_andrad",
    )


def test_the_event_platform_is_set_up_and_every_event_type_has_its_texts(const):
    import json
    import pathlib

    assert "event" in const.PLATFORMS
    root = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
    transitions = load("transitions")
    texts = {}
    for name in ("strings.json", "translations/en.json", "translations/sv.json"):
        data = json.loads((root / name).read_text(encoding="utf-8"))
        states = data["entity"]["event"]["events"]["state_attributes"]["event_type"]["state"]
        assert set(states) == set(transitions.EVENT_TYPES), name
        assert all(states.values()), name
        texts[name] = states
    # strings.json is the English source, and the two must say the same.
    assert texts["strings.json"] == texts["translations/en.json"]
    for text in texts["translations/sv.json"].values():
        assert "–" not in text and " - " not in text, text


def test_the_event_entity_is_explained_sourced_and_listed_but_not_a_chip(
    explanations, dashboard_views
):
    assert "loggboken" in explanations.explain("events")
    source = explanations.source("events")
    assert "62017" in source and "62005" in source and "62301" in source
    assert "events" in dashboard_views._KNOWN_KEYS
    assert "events" not in dashboard_views._STATUS_ROWS
    assert "events" not in dashboard_views._STATUS_TILES


def test_the_event_module_reads_the_watch_by_a_cursor_and_lists_the_types():
    # The module needs Home Assistant's event platform, which the stand-ins do
    # not carry; the wiring is checked under a real core in
    # test_homeassistant_transitions.py. Here: what it is built from.
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "custom_components" / "ctc_ecozenith" / "event.py"
    ).read_text(encoding="utf-8")
    assert "_attr_event_types = list(EVENT_TYPES)" in source
    assert "self._watch.since(self._fired)" in source
    assert "self._fired = self._watch.seq" in source
    assert '_attr_translation_key = "events"' in source
    assert "stats" not in source, "inget till backenden"


# ---------------------------------------------------- out of the coordinator


class FakeCoordinator:
    def __init__(self, data, codes):
        self.data = data
        self.codes = codes


def test_a_sample_trusts_a_code_only_for_a_reading_in_the_data(transitions):
    sample = transitions.sample_of(
        FakeCoordinator(
            {"hp1_status": "Till värme", "system_status": "Värme", "outdoor_temp": 7.2},
            {"hp1_status": 3, "system_status": 4, "sg_mode": 1},
        ),
        at(10),
    )
    assert sample == transitions.Sample(at(10), hp_status=3, system_status=4, sg_mode=None, outdoor=7.2)
    empty = transitions.sample_of(FakeCoordinator(None, None), at(10))
    assert empty == transitions.Sample(at(10))
    # The outdoor temperature is a number or nothing.
    odd = transitions.sample_of(FakeCoordinator({"outdoor_temp": "7,2"}, {}), at(10))
    assert odd.outdoor is None


# -------------------------------------------------- the sensors on the watch


def _sensor(transitions, key):
    return next(item for item in transitions.TRANSITION_SENSORS if item.key == key)


def test_the_sensor_table_reads_the_watch_before_and_after_a_start(transitions, watch):
    keys = [item.key for item in transitions.TRANSITION_SENSORS]
    assert keys == ["last_start", "starts_today", "last_run", "defrosts_today", "last_defrost"]
    kinds = {item.key: item.kind for item in transitions.TRANSITION_SENSORS}
    assert kinds["last_start"] == "timestamp"
    assert kinds["starts_today"] == "count"
    assert kinds["last_run"] == "minutes"

    feed(transitions, watch, (at(9), 1))
    last_start, starts, last_run = (_sensor(transitions, k) for k in keys[:3])
    assert last_start.value(watch) is None
    assert last_start.attributes(watch) == {
        "systemstatus vid starten": None, "utetemperatur vid starten": None, "körning pågår": False,
    }
    assert starts.value(watch) == 0
    assert starts.attributes(watch) == {"räknas sedan": "2026-10-09T09:00:00+02:00"}
    assert last_run.value(watch) is None
    assert last_run.attributes(watch) == {"startade": None, "slutade": None}

    feed(transitions, watch, (at(10), 33), system_status=5, outdoor=2.0)
    assert last_start.value(watch) == at(10)
    assert last_start.attributes(watch) == {
        "systemstatus vid starten": "Varmvatten", "utetemperatur vid starten": 2.0, "körning pågår": True,
    }
    assert starts.value(watch) == 1
    feed(transitions, watch, (at(10, 30), 1))
    assert last_run.value(watch) == 30.0
    assert last_run.attributes(watch) == {
        "startade": "2026-10-09T10:00:00+02:00", "slutade": "2026-10-09T10:30:00+02:00",
    }
    assert last_start.attributes(watch)["körning pågår"] is False


# ------------------------------------------------ the mean run over a day


def test_the_mean_run_divides_the_days_minutes_by_the_days_starts(transitions):
    # The i255's history page read 03:46 of compressor time and 8 starts the
    # day it was captured: 226 minutes over 8 starts.
    assert transitions.mean_run_minutes(226, 8) == 28.2
    assert transitions.mean_run_minutes(240.0, 8.0) == 30.0
    # No starts is no figure, not a division by zero; and nothing without both.
    assert transitions.mean_run_minutes(226, 0) is None
    assert transitions.mean_run_minutes(226, None) is None
    assert transitions.mean_run_minutes(None, 8) is None
    assert transitions.mean_run_minutes(-1, 8) is None
    assert transitions.mean_run_minutes(True, 8) is None
    assert transitions.mean_run_minutes("226", 8) is None


class FakeClient:
    """Hands async_page_values the widgets of one real screen."""

    def __init__(self, data):
        self._data = data

    async def async_widgets(self, screen):
        assert screen == self._data["screen"]
        widget = load("web_api").Widget
        return [widget(**w) for w in self._data["widgets"]]


def _harvested(catalogue, data, page_number):
    class Page:
        values = asyncio.run(
            catalogue.async_page_values(FakeClient(data), page_number, [data["screen"]])
        )

    return Page()


@pytest.mark.parametrize("name", ["i255_128", "i550_136"])
def test_the_starts_per_day_row_is_found_on_both_history_pages(transitions, catalogue, page, name):
    found = transitions.find_starts_per_day([_harvested(catalogue, page(name), 30)])
    assert found is not None
    assert found.key == "p30_antal_starter_24"
    assert found.label == "Antal starter /24"
    assert found.unit is None


@pytest.mark.parametrize("name", ["i255_118", "i550_138"])
def test_the_heat_pump_pages_have_no_starts_per_day(transitions, catalogue, page, name):
    assert transitions.find_starts_per_day([_harvested(catalogue, page(name), 22)]) is None
    assert transitions.find_starts_per_day([]) is None


def test_the_catalogue_tells_the_period_count_from_the_lifetime_counter(catalogue):
    assert catalogue.is_period_count("Antal starter /24")
    assert catalogue.is_period_count("Antal starter /24 h")
    assert catalogue.is_period_count("Number of starts /24")
    assert not catalogue.is_period_count("Antal starter")
    assert not catalogue.is_period_count("Drift /24")
    assert not catalogue.is_period_count(None)


# ------------------------------------------------------- the words around it


def _new_keys(transitions):
    return [item.key for item in transitions.TRANSITION_SENSORS] + [transitions.MEAN_RUN_KEY]


def test_every_new_sensor_is_explained_sourced_and_placed_on_the_page(
    transitions, explanations, dashboard_views
):
    for key in _new_keys(transitions):
        assert explanations.explain(key), key
        assert explanations.source(key), key
        assert key in dashboard_views._KNOWN_KEYS, key
    assert "62017" in explanations.source("last_start")
    assert "62234" in explanations.source("mean_run_24h")
    # A count since midnight that starts over with Home Assistant says so.
    assert "omstart" in explanations.explain("starts_today")
    assert "Nollas vid midnatt" in explanations.explain("starts_today")


def test_the_new_words_use_no_dashes_as_punctuation(transitions, explanations, watch):
    feed(transitions, watch, (at(9), 1), (at(10), 3), (at(11), 1))
    texts = [transitions.MEAN_RUN_NAME]
    for item in transitions.TRANSITION_SENSORS:
        texts.append(item.name)
        texts.extend(item.attributes(watch))
    for key in _new_keys(transitions):
        texts.append(explanations.explain(key))
        texts.append(explanations.source(key))
    for transition in watch.observe(transitions.Sample(at(12), hp_status=3)):
        texts.extend(transition.attributes())
    for text in texts:
        assert "–" not in text and " - " not in text and "—" not in text, text
