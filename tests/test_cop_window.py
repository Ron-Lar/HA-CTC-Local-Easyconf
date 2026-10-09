"""The coefficient of performance over a week and a month, from the samples already kept.

Between the day and the year there was nothing: a lifetime figure of 2.48
says nothing about this autumn, and the display's own "/30 dagar" rows stand
at zero on both units they have been read from. The tracker keeps one sample a
day for the year, and those samples carry a week and a month from the day they
are old enough. The two new sensors stand on the same rules as the day, scaled
to their span: the floor is three kilowatt hours per day in the figure, the
base sample may lie a little further back than the name says but not much, and
an empty figure says why. Nothing new leaves the house: the report's keys are
unchanged, so the consent text need not be.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def run(coro):
    return asyncio.run(coro)


TODAY = date(2026, 10, 9)


def _tracker(cop, samples):
    """A tracker with one sample per (days ago, out, consumed)."""
    tracker = cop.CopTracker(FakeStore())
    for ago, out, consumed in samples:
        run(tracker.async_record(out, consumed, today=TODAY - timedelta(days=ago)))
    return tracker


# ------------------------------------------------------------- the figures


def test_the_windows_are_a_week_and_a_month_with_a_little_give(cop):
    assert cop.WINDOWS == {"week": (7, 2), "month": (30, 5)}


def test_a_week_is_read_against_the_sample_seven_days_old(cop):
    # VSH's autumn: about 20 kWh of heat against 7 of electricity a day.
    tracker = _tracker(cop, [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in range(0, 12)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.basis == "week"
    assert week.days == 7
    assert (week.energy_out, week.energy_in) == (140.0, 49.0)
    assert week.value == pytest.approx(140 / 49, abs=0.01)
    assert week.reason is None
    assert week.as_attributes()["underlag"] == "senaste 7 dygnen"
    assert week.as_attributes()["dygn i underlaget"] == 7


def test_a_month_is_read_against_the_sample_thirty_days_old(cop):
    tracker = _tracker(cop, [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in range(0, 40)])
    month = tracker.result_window(22499, 9116, "month", today=TODAY)
    assert month.basis == "month" and month.days == 30
    assert (month.energy_out, month.energy_in) == (600.0, 210.0)
    assert month.value == pytest.approx(600 / 210, abs=0.01)
    assert month.as_attributes()["underlag"] == "senaste 30 dygnen"


def test_the_newest_sample_inside_the_band_is_the_base(cop):
    # Seven days ago is missing; eight is there and is used, and the span says so.
    tracker = _tracker(cop, [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in (0, 1, 8, 9, 10)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.days == 8
    assert (week.energy_out, week.energy_in) == (160.0, 56.0)
    assert week.value == pytest.approx(160 / 56, abs=0.01)


def test_a_sample_beyond_the_give_does_not_pass_for_a_week(cop):
    # Only a sample ten days old: a week may stretch to nine days, not ten.
    tracker = _tracker(cop, [(0, 22499, 9116), (10, 22299, 9046)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.value is None
    assert week.reason == "inget sparat prov är 7 till 9 dygn gammalt"
    assert week.days == 7
    # The same two samples make neither a month.
    month = tracker.result_window(22499, 9116, "month", today=TODAY)
    assert month.value is None
    assert month.reason == "10 av 30 dygn samlade, äldsta provet 2026-09-29"
    assert month.days == 10


def test_the_month_stretches_to_thirty_five_days_and_no_further(cop):
    tracker = _tracker(cop, [(0, 22499, 9116), (35, 21799, 8871)])
    assert tracker.result_window(22499, 9116, "month", today=TODAY).days == 35
    tracker = _tracker(cop, [(0, 22499, 9116), (36, 21779, 8864)])
    month = tracker.result_window(22499, 9116, "month", today=TODAY)
    assert month.value is None
    assert month.reason == "inget sparat prov är 30 till 35 dygn gammalt"


# ------------------------------------------------------ the scaled floor


def test_the_floor_is_three_kilowatt_hours_per_day_in_the_figure(cop):
    # Summer: 20 kWh consumed over a week is under 21, over eight days under 24.
    tracker = _tracker(cop, [(0, 22499, 9116), (7, 22439, 9096)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.value is None
    assert (week.energy_out, week.energy_in) == (60.0, 20.0)
    assert cop.cop_reason(None, "week", 60.0, 20.0, 20000, days=7) == (
        "för lite energi ännu, 20.0 av 21 kWh"
    )
    # One more kilowatt hour and there is a figure.
    assert tracker.result_window(22502, 9117, "week", today=TODAY).value == pytest.approx(63 / 21, abs=0.01)
    # Eight days in the figure raise the floor to 24.
    tracker = _tracker(cop, [(0, 22499, 9116), (8, 22439, 9094)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.days == 8 and week.value is None and week.energy_in == 22.0
    assert "22.0 av 24 kWh" in cop.cop_reason(None, "week", 60.0, 22.0, 20000, days=8)


def test_a_month_needs_ninety_kilowatt_hours(cop):
    tracker = _tracker(cop, [(0, 22499, 9116), (30, 22249, 9027)])
    month = tracker.result_window(22499, 9116, "month", today=TODAY)
    assert month.value is None and month.energy_in == 89.0
    assert "89.0 av 90 kWh" in cop.cop_reason(None, "month", 250.0, 89.0, 20000, days=30)
    assert tracker.result_window(22499, 9117, "month", today=TODAY).value == pytest.approx(250 / 90, abs=0.01)


def test_a_still_week_is_too_little_energy_not_a_stuck_counter(cop):
    # A week in July without a compressor start, on a unit powered for years.
    assert cop.cop_reason(None, "week", 0.0, 0.0, 20000, days=7) == (
        "för lite energi ännu, 0.0 av 21 kWh"
    )
    assert "står på noll" not in cop.cop_reason(None, "month", 0.0, 0.0, 20000, days=30)


def test_an_impossible_week_is_still_said_so_above_the_floor(cop):
    assert "orimlig" in cop.cop_reason(None, "week", 300.0, 21.0, 20000, days=7)
    assert "för lite energi" in cop.cop_reason(None, "week", 300.0, 20.0, 20000, days=7)


# -------------------------------------------------------------- the reasons


def test_a_fresh_installation_says_how_far_the_week_has_come(cop):
    tracker = _tracker(cop, [(3, 22439, 9095), (0, 22499, 9116)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.value is None and week.days == 3
    assert week.reason == "3 av 7 dygn samlade, äldsta provet 2026-10-06"
    assert cop.reason_for(week, 22499.0, 9116.0, 20000) == week.reason
    month = tracker.result_window(22499, 9116, "month", today=TODAY)
    assert month.reason == "3 av 30 dygn samlade, äldsta provet 2026-10-06"


def test_no_sample_at_all_is_said_so(cop):
    week = cop.CopTracker(FakeStore()).result_window(22499, 9116, "week", today=TODAY)
    assert week == cop.CopResult(None, "week", 0, reason="0 av 7 dygn samlade, inget prov sparat ännu")


def test_unread_counters_leave_the_window_empty_without_a_progress_text(cop):
    tracker = _tracker(cop, [(7, 22439, 9095)])
    week = tracker.result_window(None, None, "week", today=TODAY)
    assert week == cop.CopResult(None, "week", 0)
    assert cop.reason_for(week, None, None) == "räknarna har inte lästs"


def test_counters_going_backwards_are_said_so_for_a_window(cop):
    tracker = _tracker(cop, [(7, 30000, 12000)])
    week = tracker.result_window(22499, 9116, "week", today=TODAY)
    assert week.value is None
    assert week.reason == "räknarna har gått bakåt sedan 2026-10-02, enheten är bytt eller nollställd"


def test_a_window_asks_for_a_known_span_only(cop):
    with pytest.raises(KeyError):
        cop.CopTracker(FakeStore()).result_window(22499, 9116, "fortnight", today=TODAY)


# -------------------------------------------------------- the two sensors


@pytest.fixture(scope="module")
def sensor():
    ha_stub.skip_unless_stubbed()
    return load("sensor")


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
REAL_TODAY = date.today()


def _runtime(cop, samples, data=None, success=True):
    tracker = cop.CopTracker(FakeStore())
    for ago, out, consumed in samples:
        run(tracker.async_record(out, consumed, today=REAL_TODAY - timedelta(days=ago)))
    if data is None:
        data = {"out": 22499.0, "in": 9116.0}
    return SimpleNamespace(
        cop=tracker,
        web=SimpleNamespace(
            data=data, last_update_success=success,
            read_at={key: NOW for key in data},
        ),
        modbus=SimpleNamespace(answered={62341}, descriptions=[]),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in"),
        consumption_snapshot=None,
        operating_hours=None,
        pages=[],
        identity=SimpleNamespace(
            heatpump_model=None, display_firmware=None, heatpump_firmware=None,
            bootloader=None, serial=None, manufactured=None,
        ),
    )


def _sensors(sensor, runtime):
    added = []
    run(sensor.async_setup_entry(None, SimpleNamespace(runtime_data=runtime, entry_id="test", async_on_unload=lambda f: None), added.extend))
    return {s._span: s for s in added if isinstance(s, sensor.CtcCopSensor)}


def test_the_week_and_the_month_are_sensors_with_their_own_keys(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop, []))
    assert list(sensors) == ["day", "week", "month", "year", "first_year", "lifetime"]
    assert sensors["week"]._attr_unique_id == "ctc_ecozenith_pump_cop_week"
    assert sensors["month"]._attr_unique_id == "ctc_ecozenith_pump_cop_month"
    assert sensors["week"]._attr_name == "Veckovärmefaktor"
    assert sensors["month"]._attr_name == "Månadsvärmefaktor"
    assert sensors["week"]._attr_state_class == sensors["day"]._attr_state_class


def test_the_two_sensors_show_their_figures_and_attributes(sensor, cop):
    samples = [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in range(0, 40)]
    sensors = _sensors(sensor, _runtime(cop, samples))
    week = sensors["week"]
    assert week.native_value == pytest.approx(140 / 49, abs=0.01)
    assert week.extra_state_attributes["underlag"] == "senaste 7 dygnen"
    assert week.extra_state_attributes["dygn i underlaget"] == 7
    assert week.extra_state_attributes["avgiven värme kWh"] == 140.0
    assert "skäl" not in week.extra_state_attributes
    month = sensors["month"]
    assert month.native_value == pytest.approx(600 / 210, abs=0.01)
    assert month.extra_state_attributes["underlag"] == "senaste 30 dygnen"
    assert month.extra_state_attributes["dygn i underlaget"] == 30


def test_the_two_sensors_say_why_they_are_empty(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop, [(3, 22439.0, 9095.0)]))
    assert sensors["week"].native_value is None
    assert sensors["week"].extra_state_attributes["skäl"] == (
        f"3 av 7 dygn samlade, äldsta provet {REAL_TODAY - timedelta(days=3)}"
    )
    assert sensors["month"].extra_state_attributes["skäl"].startswith("3 av 30 dygn samlade")
    still = _sensors(sensor, _runtime(cop, [(7, 22499.0, 9116.0)]))
    assert still["week"].extra_state_attributes["skäl"] == "för lite energi ännu, 0.0 av 21 kWh"


def test_a_quiet_display_leaves_the_two_sensors_available_with_the_notice(sensor, cop):
    samples = [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in range(0, 40)]
    sensors = _sensors(sensor, _runtime(cop, samples, success=False))
    for span in ("week", "month"):
        assert sensors[span].available is True
        assert sensors[span].native_value is None
        assert sensors[span].extra_state_attributes["skäl"] == (
            "displayen har inte svarat sedan 2026-10-09T12:00:00+00:00"
        )


# ------------------------------------------------------- nothing new is sent


def test_the_report_carries_no_week_or_month(cop, stats_extra):
    assert "cop_week" not in cop.COP_REPORT_KEYS and "cop_month" not in cop.COP_REPORT_KEYS
    assert "cop_week" not in stats_extra.METRIC_KEYS and "cop_month" not in stats_extra.METRIC_KEYS
    samples = [(ago, 22499 - 20 * ago, 9116 - 7 * ago) for ago in range(0, 40)]
    figures = cop.cop_for_report(_runtime(cop, samples))
    assert set(figures) == set(cop.COP_REPORT_KEYS)


# ------------------------------------------------------- the page knows them


def test_the_page_explains_the_two_and_places_them_with_the_energy(explanations, dashboard_views):
    for key in ("cop_week", "cop_month"):
        assert explanations.explain(key), key
        assert explanations.source(key) == "Räknas ur energiräknarna"
        assert key in dashboard_views._KNOWN_KEYS
    energy = next(keys for _id, _icon, keys in dashboard_views._READINGS if _id == "energy")
    assert energy[:6] == ("cop_day", "cop_week", "cop_month", "cop_year", "cop_first_year", "cop_lifetime")
