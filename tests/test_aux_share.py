"""The share of auxiliary heat, Spetsvärmeandel, without Home Assistant (roadmap R28).

How much of the electricity went to the immersion heater: its counter, Modbus
62191, against the sum of it and the compressor's counter, the consumed energy
the coefficient of performance divides by, over the coefficient of
performance's spans but the first year. The tracker keeps the immersion
heater's counter as a third number in its samples (store minor 2); a sample
without it, every sample from before, is unknown and passed over, and the
release before reads the new file as it read the old one. A hundred per cent
only where the compressor's counter stood still while the immersion heater's
moved, as PT's i550 Pro (912 kWh against 0); VSH's i255 (1 of 9358 kWh) is
0.0. Without a figure the reason is an attribute, as on the coefficient of
performance. Nothing of it reaches the statistics backend. Under a real core
the same runs in test_homeassistant_aux_share.py.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import inspect
import json
import re
from types import SimpleNamespace

import pytest

from conftest import COMPONENT, load
import ha_stub


class FakeStore:
    """Stands in for Home Assistant's Store, in memory, through JSON like the file."""

    def __init__(self, data=None) -> None:
        self.data = data

    async def async_load(self):
        return None if self.data is None else json.loads(json.dumps(self.data))

    async def async_save(self, data):
        self.data = json.loads(json.dumps(data))


def run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


def _tracker(cop, data=None):
    tracker = cop.CopTracker(FakeStore(data))
    run(tracker.async_load())
    return tracker


# ------------------------------------------------------------ the store


def test_a_sample_keeps_the_immersion_heater_as_a_third_number_where_it_was_read(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    read = NOW - timedelta(hours=24)
    run(tracker.async_record(22499.0, 9116.0, now=read, immersion=1.0))
    day = read.date().isoformat()
    assert store.data["samples"][day] == [22499.0, 9116.0, 1.0]
    assert store.data["recent"] == [[read.isoformat(), 22499.0, 9116.0, 1.0]]


def test_a_sample_without_it_is_stored_as_minor_one_stored_every_sample(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    read = NOW - timedelta(hours=24)
    run(tracker.async_record(22499.0, 9116.0, now=read))
    assert store.data["samples"][read.date().isoformat()] == [22499.0, 9116.0]
    assert store.data["recent"] == [[read.isoformat(), 22499.0, 9116.0]]
    # Something that is no reading of a counter is no third number either.
    for junk in (-1.0, True, "912"):
        store = FakeStore()
        run(cop.CopTracker(store).async_record(1.0, 2.0, now=read, immersion=junk))
        assert store.data["recent"] == [[read.isoformat(), 1.0, 2.0]], junk


def test_a_file_minor_one_wrote_is_read_and_its_immersion_heater_is_unknown(cop):
    based = NOW - timedelta(hours=24)
    old = {
        "samples": {(TODAY - timedelta(days=7)).isoformat(): [22400.0, 9100.0]},
        "recent": [[based.isoformat(), 22480.0, 9110.0]],
        "anchor": "2025-10-10",
        "first_year": [22000.0, 8900.0, 366.0],
    }
    tracker = _tracker(cop, old)
    # The coefficient of performance reads it exactly as before.
    assert tracker.result_day(22499.0, 9116.0, now=NOW).value == pytest.approx(19 / 6, abs=0.01)
    assert tracker.result_window(22510.0, 9130.0, "week", today=TODAY).days == 7
    assert tracker.result_first_year().value == pytest.approx(22000 / 8900, abs=0.01)
    # The share passes the samples over, saying why.
    for share in (
        tracker.share_day(9116.0, 1.0, now=NOW),
        tracker.share_window(9130.0, 1.0, "week", today=TODAY),
    ):
        assert share.value is None and share.reason == cop.SHARE_NOT_SAMPLED


def _read_as_the_release_before(data):
    """How 0.21.1's CopTracker.async_load reads the file, copied from it.

    0.21.1 is what a return from this release lands on, and it opens the file
    at minor 1: Home Assistant hands it the minor 2 data as it is, since the
    major is the same. These are its rules for the three parts it reads.
    """
    samples = {
        day: [float(values[0]), float(values[1])]
        for day, values in data["samples"].items()
        if isinstance(values, (list, tuple)) and len(values) >= 2
    }
    first = data.get("first_year")
    first_year = (
        [float(first[0]), float(first[1]), float(first[2])]
        if isinstance(first, (list, tuple)) and len(first) >= 3 else None
    )
    recent = []
    for row in data["recent"]:
        if isinstance(row, (list, tuple)) and len(row) >= 3:
            recent.append((str(row[0]), float(row[1]), float(row[2])))
    return samples, recent, first_year


def test_the_release_before_reads_the_new_file_and_keeps_every_sample(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    run(tracker.async_set_anchor(TODAY - timedelta(days=400)))
    run(tracker.async_record(20000.0, 8000.0, today=TODAY - timedelta(days=35),
                             now=NOW - timedelta(days=35), immersion=1.0))
    run(tracker.async_record(22000.0, 8900.0, today=TODAY - timedelta(days=2),
                             now=NOW - timedelta(days=2)))
    run(tracker.async_record(22499.0, 9116.0, now=NOW - timedelta(hours=24), immersion=1.0))
    samples, recent, first_year = _read_as_the_release_before(store.data)
    assert samples == {day: values[:2] for day, values in tracker._samples.items()}
    assert recent == [tuple(row[:3]) for row in tracker._recent]
    assert first_year == tracker._first_year
    # Three days kept; the run of recent samples keeps four days, so two.
    assert len(samples) == 3 and len(recent) == 2
    assert any(len(values) == 3 for values in store.data["samples"].values())


def test_the_store_stays_at_major_one_and_is_opened_at_minor_two(cop):
    assert cop.STORAGE_VERSION == 1
    assert cop.STORAGE_MINOR_VERSION == 2
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "STORAGE_MINOR_VERSION as COP_MINOR_VERSION" in source
    assert 'Store(hass, 1, f"{DOMAIN}_{entry.entry_id}_cop", minor_version=COP_MINOR_VERSION)' in (
        source
    )
    # A sample takes the immersion heater's counter of its own moment.
    record = source.split("async def async_record_cop(")[1].split("\n\n\n")[0]
    assert "immersion=immersion_at(self, read_at)" in record


# --------------------------------------------------------------- the edges


def test_a_hundred_per_cent_only_when_the_compressor_stood_still(cop):
    assert cop.share_value(912.0, 0.0) == 100.0
    # 2000 against 0.4 rounds to 100.0, but the compressor did use something.
    assert cop.share_value(2000.0, 0.4) == 99.9
    assert cop.share_value(5.0, 5.0) == 50.0
    assert cop.share_value(1.0, 9357.0) == 0.0


def test_vsh_over_the_lifetime_is_nought_with_the_kilowatt_hours_beside_it(cop):
    share = _tracker(cop).share_lifetime(9357.0, 1.0, today=TODAY, hours=8044.0)
    assert (share.value, share.reason) == (0.0, None)
    assert (share.immersion, share.compressor) == (1.0, 9357.0)
    assert share.as_attributes() == {
        "underlag": "hela livslängden", "dygn i underlaget": 0,
        "elpatron kWh": 1.0, "kompressor kWh": 9357.0,
    }


def test_pt_over_the_lifetime_is_a_hundred_per_cent(cop):
    share = _tracker(cop).share_lifetime(0.0, 912.0, today=TODAY, hours=9000.0)
    assert (share.value, share.reason) == (100.0, None)
    assert (share.immersion, share.compressor) == (912.0, 0.0)


def test_both_lifetime_counters_at_zero_are_stuck_on_a_unit_that_has_run(cop):
    tracker = _tracker(cop)
    stuck = tracker.share_lifetime(0.0, 0.0, today=TODAY, hours=cop.RUNNING_HOURS)
    assert stuck.value is None and stuck.reason == cop.SHARE_BOTH_STUCK
    for hours in (2.0, None):
        new = tracker.share_lifetime(0.0, 0.0, today=TODAY, hours=hours)
        assert new.reason == "för lite energi ännu, 0.0 av 10 kWh", hours
    assert tracker.share_lifetime(3.0, 2.0, today=TODAY).reason == (
        "för lite energi ännu, 5.0 av 10 kWh"
    )
    assert tracker.share_lifetime(None, 2.0).value is None
    assert tracker.share_lifetime(3.0, None).value is None


# ----------------------------------------------------------------- the day


def test_the_day_is_read_against_the_newest_sample_with_the_counter(cop):
    tracker = _tracker(cop)
    run(tracker.async_record(22400.0, 9100.0, now=NOW - timedelta(hours=26), immersion=0.0))
    # Newer and in the band, but written down without the counter.
    run(tracker.async_record(22420.0, 9104.0, now=NOW - timedelta(hours=22)))
    share = tracker.share_day(9120.0, 5.0, now=NOW)
    assert share.value == 20.0, "5 kWh elpatron mot 20 kWh kompressor"
    assert (share.immersion, share.compressor, share.days) == (5.0, 20.0, 1)
    # The coefficient of performance still takes the newest sample.
    assert tracker.result_day(22480.0, 9120.0, now=NOW).energy_in == 16.0


def test_the_day_waits_for_a_sample_and_says_why(cop):
    tracker = _tracker(cop)
    assert tracker.share_day(9120.0, 5.0, now=NOW).reason == (
        "väntar på ett prov som är 20 till 30 timmar gammalt"
    )
    run(tracker.async_record(22400.0, 9100.0, now=NOW - timedelta(hours=24)))
    assert tracker.share_day(9120.0, 5.0, now=NOW).reason == cop.SHARE_NOT_SAMPLED
    assert tracker.share_day(None, 5.0, now=NOW).value is None


def test_pt_over_a_day_is_a_hundred_per_cent_and_still_counters_say_so(cop):
    tracker = _tracker(cop)
    base = NOW - timedelta(hours=24)
    run(tracker.async_record(0.0, 0.0, now=base, immersion=900.0))
    share = tracker.share_day(0.0, 912.0, now=NOW)
    assert (share.value, share.immersion, share.compressor) == (100.0, 12.0, 0.0)
    still = tracker.share_day(0.0, 900.0, now=NOW)
    assert still.value is None
    since = base.isoformat(timespec="minutes")
    assert still.reason == f"båda räknarna har stått still sedan {since}"


def test_too_little_energy_and_counters_gone_backwards_give_no_day(cop):
    tracker = _tracker(cop)
    base = NOW - timedelta(hours=24)
    run(tracker.async_record(22400.0, 9100.0, now=base, immersion=10.0))
    assert tracker.share_day(9101.0, 11.0, now=NOW).reason == "för lite energi ännu, 2.0 av 3 kWh"
    backwards = tracker.share_day(9120.0, 2.0, now=NOW)
    assert backwards.value is None
    assert backwards.reason.startswith("räknarna har gått bakåt sedan")


# ------------------------------------------------------------- the windows


def test_the_week_month_and_year_use_the_coefficient_of_performances_bands(cop):
    tracker = _tracker(cop)
    run(tracker.async_record(20000.0, 8000.0, today=TODAY - timedelta(days=366),
                             now=NOW - timedelta(days=366), immersion=0.0))
    run(tracker.async_record(22000.0, 9000.0, today=TODAY - timedelta(days=30),
                             now=NOW - timedelta(days=30), immersion=0.0))
    run(tracker.async_record(22400.0, 9100.0, today=TODAY - timedelta(days=7),
                             now=NOW - timedelta(days=7), immersion=0.0))
    week = tracker.share_window(9121.0, 7.0, "week", today=TODAY)
    assert (week.value, week.days) == (25.0, 7)
    month = tracker.share_window(9121.0, 7.0, "month", today=TODAY)
    assert (month.value, month.days) == (5.5, 30)  # 7 of 128
    year = tracker.share_year(9121.0, 7.0, today=TODAY)
    assert (year.value, year.days) == (0.6, 366)  # 7 of 1128
    # Each stands on the same span the coefficient of performance does.
    assert tracker.result_window(22500.0, 9121.0, "week", today=TODAY).days == week.days
    assert tracker.result_year(22500.0, 9121.0, today=TODAY).days == year.days


def test_a_week_floor_is_three_a_day_on_the_two_together(cop):
    tracker = _tracker(cop)
    run(tracker.async_record(22400.0, 9100.0, today=TODAY - timedelta(days=7),
                             now=NOW - timedelta(days=7), immersion=0.0))
    assert tracker.share_window(9110.0, 10.0, "week", today=TODAY).reason == (
        "för lite energi ännu, 20.0 av 21 kWh"
    )
    assert tracker.share_window(9111.0, 10.0, "week", today=TODAY).value == pytest.approx(47.6)


def test_a_window_without_a_sample_with_the_counter_waits_and_says_why(cop):
    tracker = _tracker(cop)
    assert tracker.share_window(9121.0, 7.0, "week", today=TODAY).reason == (
        "0 av 7 dygn samlade, inget prov sparat ännu"
    )
    run(tracker.async_record(22400.0, 9100.0, today=TODAY - timedelta(days=7),
                             now=NOW - timedelta(days=7)))
    assert tracker.share_window(9121.0, 7.0, "week", today=TODAY).reason == cop.SHARE_NOT_SAMPLED
    assert tracker.share_window(9121.0, 7.0, "month", today=TODAY).reason == (
        f"7 av 30 dygn samlade, äldsta provet {TODAY - timedelta(days=7)}"
    )
    assert tracker.share_year(9121.0, 7.0, today=TODAY).value is None


# ------------------------------------------------------ the counters' faults


def test_a_compressor_counter_at_zero_on_a_compressor_that_has_run_is_a_fault(cop):
    assert cop.compressor_counter_fault(0.0, 100.0) == (
        "kompressorns räknare står på noll fast kompressorn har gått 100 timmar, "
        "styrenheten fyller den inte"
    )
    assert cop.compressor_counter_fault(0.0, cop.RUNNING_HOURS - 1) is None
    assert cop.compressor_counter_fault(5.0, 1000.0) is None
    assert cop.compressor_counter_fault(0.0, None) is None
    assert cop.compressor_counter_fault(None, 1000.0) is None


def test_the_reason_takes_the_nearest_cause_first(cop):
    figure = cop.ShareResult(100.0, "lifetime", 10, 912.0, 0.0)
    reason = cop.share_reason
    assert reason(figure, 0.0, 912.0, display="tyst") == "tyst"
    assert reason(figure, None, 912.0, consumption_answered=False) == (
        "registret 62341 har inte svarat, så tillförd energi saknas"
    )
    assert reason(figure, None, 912.0) == "räknarna har inte lästs"
    assert reason(figure, 0.0, None, immersion_answered=False) == cop.SHARE_IMMERSION_SILENT
    assert reason(figure, 0.0, None) == cop.SHARE_IMMERSION_UNREAD
    assert reason(figure, 0.0, 912.0, compressor_hours=40.0).startswith(
        "kompressorns räknare står på noll"
    )
    assert reason(figure, 0.0, 912.0, compressor_hours=0.0) is None
    empty = cop.ShareResult(None, "day", 0, reason="väntar")
    assert reason(empty, 0.0, 912.0) == "väntar"


# ----------------------------------------------------------- the snapshot


def test_the_immersion_heater_is_taken_when_the_display_is_read(cop):
    first = NOW - timedelta(minutes=40)
    later = NOW - timedelta(minutes=10)
    snapshot = cop.ImmersionSnapshot()
    # After a restart: the moment the display store came back with, no value.
    snapshot.seed(first, None)
    snapshot.update(first, {"immersion_kwh": 912})
    assert snapshot.value is None, "inget färskt Modbusvärde ihop med en gammal avläsning"
    snapshot.update(later, {"immersion_kwh": 912, "compressor_kwh": 5})
    assert snapshot.value == 912.0
    assert snapshot.value_at(later) == 912.0
    assert snapshot.value_at(first) is None and snapshot.value_at(None) is None
    # A round that failed, or no reading, gives nothing for that moment.
    nothing = (None, {}, {"immersion_kwh": 9999}, {"immersion_kwh": -1}, {"immersion_kwh": True})
    for minutes, data in enumerate(nothing, start=1):
        snapshot.update(later + timedelta(minutes=minutes), {"immersion_kwh": 900})
        assert snapshot.value == 900.0
        snapshot.update(later + timedelta(minutes=minutes, seconds=30), data)
        assert snapshot.value is None, data
    # The consumption snapshot is untouched by the generalisation.
    consumption = cop.ConsumptionSnapshot()
    consumption.update(later, {"immersion_kwh": 912, "compressor_kwh": 5})
    assert consumption.value == 5.0
    assert cop.MODBUS_IMMERSION_ADDRESS == 62191
    assert cop.modbus_immersion_answered({62191}) and not cop.modbus_immersion_answered(None)


# ------------------------------------------------ a heat pump's runtime


def _runtime(cop, *, out, consumed, immersion, compressor_hours, success=True,
             answered=frozenset({62341, 62191}), tracker=None):
    data = {"out": out, "in": consumed, "hours": 9000.0}
    web = SimpleNamespace(
        data=data,
        last_update_success=success,
        read_at={key: NOW - timedelta(minutes=20) for key in data},
    )
    return SimpleNamespace(
        cop=tracker or _tracker(cop),
        web=web,
        modbus=SimpleNamespace(
            answered=set(answered), descriptions=[],
            data={"compressor_hours": compressor_hours},
        ),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in"),
        consumption_snapshot=None,
        immersion_snapshot=SimpleNamespace(value=immersion),
        operating_hours=[SimpleNamespace(key="hours")],
        alarms=None, seen=None, transitions=None, starts_per_day=None, pages=[],
        identity=SimpleNamespace(
            heatpump_model=None, display_firmware=None, heatpump_firmware=None,
            bootloader=None, serial=None, manufactured=None,
        ),
    )


def test_pt_is_a_hundred_per_cent_and_vsh_nought(cop):
    pt = _runtime(cop, out=0.0, consumed=0.0, immersion=912.0, compressor_hours=0.0)
    share, reason = cop.share_for(pt, "lifetime")
    assert (share.value, reason) == (100.0, None)
    vsh = _runtime(cop, out=22499.0, consumed=9357.0, immersion=1.0, compressor_hours=8000.0)
    share, reason = cop.share_for(vsh, "lifetime")
    assert (share.value, reason, share.immersion, share.compressor) == (0.0, None, 1.0, 9357.0)


def test_a_compressor_that_runs_without_counting_leaves_no_share(cop):
    pt = _runtime(cop, out=0.0, consumed=0.0, immersion=912.0, compressor_hours=40.0)
    for span in cop.SHARE_SPANS:
        share, reason = cop.share_for(pt, span)
        assert share.value is None, span
        assert reason.startswith("kompressorns räknare står på noll"), span


def test_a_quiet_display_or_a_silent_register_empties_the_share(cop):
    quiet = _runtime(cop, out=0.0, consumed=0.0, immersion=912.0, compressor_hours=0.0,
                     success=False)
    share, reason = cop.share_for(quiet, "lifetime")
    assert share.value is None and reason.startswith("displayen har inte svarat sedan")
    silent = _runtime(cop, out=0.0, consumed=0.0, immersion=None, compressor_hours=0.0,
                      answered={62341})
    assert cop.share_for(silent, "lifetime")[1] == cop.SHARE_IMMERSION_SILENT
    unread = _runtime(cop, out=0.0, consumed=0.0, immersion=None, compressor_hours=0.0)
    assert cop.share_for(unread, "lifetime")[1] == cop.SHARE_IMMERSION_UNREAD


# ------------------------------------------------------------- the sensors


@pytest.fixture(scope="module")
def sensor():
    ha_stub.skip_unless_stubbed()
    return load("sensor")


def _sensors(sensor, runtime):
    added = []
    entry = SimpleNamespace(
        runtime_data=runtime, entry_id="test", options={}, async_on_unload=lambda f: None
    )
    run(sensor.async_setup_entry(None, entry, added.extend))
    return {s._span: s for s in added if isinstance(s, sensor.CtcShareSensor)}


def test_a_share_sensor_per_span_wherever_there_is_a_coefficient_of_performance(sensor, cop):
    shares = _sensors(sensor, _runtime(cop, out=0.0, consumed=0.0, immersion=912.0,
                                       compressor_hours=0.0))
    assert tuple(shares) == cop.SHARE_SPANS == ("day", "week", "month", "year", "lifetime")
    for span, entity in shares.items():
        assert entity._attr_unique_id == f"ctc_ecozenith_pump_aux_share_{span}"
        # Named by its translation (R23): sv.json carries NAMES word for word.
        assert entity._attr_translation_key == f"aux_share_{span}"
        assert "_attr_name" not in vars(entity)
        assert sensor.CtcShareSensor.NAMES[span].startswith("Spetsvärmeandel")
        assert entity._attr_native_unit_of_measurement == "%"
        assert entity._attr_suggested_display_precision == 1
        assert entity._attr_state_class == sensor.SensorStateClass.MEASUREMENT
    without = _runtime(cop, out=0.0, consumed=0.0, immersion=912.0, compressor_hours=0.0)
    without.cop = None
    assert _sensors(sensor, without) == {}


def test_the_sensors_show_the_figure_or_the_reason_in_both_languages(sensor, cop):
    shares = _sensors(sensor, _runtime(cop, out=22499.0, consumed=9357.0, immersion=1.0,
                                       compressor_hours=8000.0))
    lifetime = shares["lifetime"]
    assert lifetime.native_value == 0.0
    shown = lifetime.extra_state_attributes
    assert shown["elpatron kWh"] == shown["immersion_kwh"] == 1.0
    assert shown["kompressor kWh"] == shown["compressor_kwh"] == 9357.0
    assert shown["underlag"] == shown["basis"] == "hela livslängden"
    assert "skäl" not in shown and "reason" not in shown
    day = shares["day"]
    assert day.native_value is None
    assert day.extra_state_attributes["skäl"] == day.extra_state_attributes["reason"] == (
        "väntar på ett prov som är 20 till 30 timmar gammalt"
    )
    assert all(entity.available for entity in shares.values())


def test_the_day_goes_with_a_quiet_display_and_the_others_say_why(sensor, cop):
    shares = _sensors(sensor, _runtime(cop, out=0.0, consumed=0.0, immersion=912.0,
                                       compressor_hours=0.0, success=False))
    assert shares["day"].available is False
    for span in ("week", "month", "year", "lifetime"):
        assert shares[span].available is True, span
        assert shares[span].native_value is None, span
        assert shares[span].extra_state_attributes["skäl"].startswith("displayen har inte svarat")


# ------------------------------------------------------- the explanations


def test_every_span_is_explained_with_the_trackers_numbers(cop, const, explanations):
    texts = {span: explanations.explain(f"aux_share_{span}") for span in cop.SHARE_SPANS}
    floor_day = f"{cop.MIN_CONSUMPTION_KWH_DAY:.0f}"
    assert f"{cop.DAY_MIN_HOURS} till {cop.DAY_MAX_HOURS} timmar" in texts["day"]
    assert f"minst {floor_day} kWh" in texts["day"]
    for span in ("week", "month"):
        days, tolerance = cop.WINDOWS[span]
        assert f"senaste {days} dygnen" in texts[span].casefold(), span
        assert f"avläsning {days} till {days + tolerance} dygn" in texts[span], span
        assert f"{cop.MIN_CONSUMPTION_KWH_DAY * days:.0f} kWh" in texts[span], span
    assert f"{const.COP_WINDOW_DAYS} till {cop.YEAR_MAX_DAYS} dagar" in texts["year"]
    for span in ("year", "lifetime"):
        assert f"minst {cop.MIN_CONSUMPTION_KWH:.0f} kWh" in texts[span], span
    for span, text in texts.items():
        assert "62191" in text, span
        assert "100 procent visas bara när kompressorns räknare har stått still" in text, span
        assert f"minst {cop.RUNNING_HOURS} timmar (register 62214)" in text, span
        assert "skäl (reason)" in text and "immersion_kwh" in text, span
        assert "30 dagar" in text and "skickas inte med statistiken" in text, span
        assert "–" not in text and "—" not in text, span
        source = explanations.source(f"aux_share_{span}")
        assert "Modbus-register 62191" in source, span


# ----------------------------------------------------------------- the page


def _pump_with_shares(pumps):
    pump = pumps["vsh"]
    for span in ("day", "week", "month", "year", "lifetime"):
        key = f"aux_share_{span}"
        pump["entities"][key] = f"sensor.ctc_ecozenith_i255_spetsvarmeandel_{span}"
        pump["names"][key] = f"Spetsvärmeandel, {span}"
    return pump


def test_the_shares_stand_beside_the_coefficient_of_performance(dashboard_views, pumps):
    pump = _pump_with_shares(pumps)
    # VSH's day is a true zero, which is an answer, not missing hardware.
    pump["unused"] = [*(pump.get("unused") or []), "aux_share_day"]
    sections = dashboard_views.performance_sections(pump, dashboard_views.TEXT["sv"], (2026, 9))
    energy = next(s for s in sections if s["cards"][0]["heading"] == "Energi och värmefaktor")
    rows = energy["cards"][1]["rows"]
    entities = [row["entity"] for row in rows]
    shares = [pump["entities"][f"aux_share_{span}"]
              for span in ("day", "week", "month", "year", "lifetime")]
    assert all(entity in entities for entity in shares)
    lifetime_cop = entities.index(pump["entities"]["cop_lifetime"])
    assert entities.index(shares[0]) == lifetime_cop + 1
    for row in rows:
        if row["entity"] in shares:
            assert row["sub"] == ["underlag", "basis"]
            assert row["reason"] == ["skäl", "reason"]
            assert row["show_reason"] is True
            assert "Spetsvärmeandel" in row["explanation"]


def test_the_shares_are_in_the_full_list_and_known_to_the_page(dashboard_views, cop):
    known = dashboard_views._KNOWN_KEYS
    assert {f"aux_share_{span}" for span in cop.SHARE_SPANS} <= known


# --------------------------------------------------------------- the report


def test_nothing_of_the_share_reaches_the_statistics(stats_extra, cop):
    words = re.compile(r"share|aux|immersion|spets")
    assert not any(words.search(key) for key in stats_extra.METRIC_KEYS)
    assert not any(words.search(key) for key in stats_extra.FEATURE_KEYS)
    parameters = inspect.signature(stats_extra.build_extra).parameters
    assert not any(words.search(name) for name in parameters)
    assert cop.COP_REPORT_KEYS == ("cop_day", "cop_year", "cop_first_year", "cop_lifetime")
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    report = source.split("def _stats_extra_for(")[1].split("\nasync def ")[0]
    assert "share" not in report and "immersion" not in report


def test_a_figure_off_the_display_thirty_day_rows_is_never_taken(cop):
    source = (COMPONENT / "cop.py").read_text(encoding="utf-8")
    share = source.split("#: The spans the share is given over.")[1].split("class CopTracker")[0]
    share += source.split("def share_day(")[1].split("#: The older name")[0]
    assert "PERIOD_MARKERS" not in share and "/30" not in share.replace('"/30 dagar"', "")


def test_the_dates_in_the_day_reason_are_minutes_not_seconds(cop):
    tracker = _tracker(cop)
    base = datetime(2026, 10, 12, 11, 59, 33, tzinfo=UTC)
    run(tracker.async_record(0.0, 0.0, now=base, immersion=1.0))
    reason = tracker.share_day(0.0, 1.0, now=base + timedelta(hours=24)).reason
    assert reason == "båda räknarna har stått still sedan 2026-10-12T11:59+00:00"
    assert isinstance(date.fromisoformat(reason.split("sedan ")[1][:10]), date)
