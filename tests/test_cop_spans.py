"""Every coefficient of performance span stands on its own and says why it is empty.

What 0.16.0 would have shown from 2026-10-10, when the anchor 2025-10-10
reached the year's band: result() answered basis year for fifteen days, so
the lifetime sensor said "rullande år" under its figure and the yearly sensor
showed the first anniversary's figure, after which it went empty until
September 2027 without a word. The first year was created on an i550 Pro whose
history page has no powered-on hours and would have stayed empty for ever. All
four went unavailable when the display was quiet three rounds, although three
of them rest on stored samples. And counter_fault judged 11 against 1 kWh an
impossible pair and sent the two totals to the statistics backend. Each has
its own answer here, all in cop.py.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------ (1) the lifetime, by name


def test_the_lifetime_figure_is_never_called_a_year(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(18000, 7500, today=today - timedelta(days=366)))
    # result() still hands out the year to whoever wants one number.
    assert tracker.result(22421, 9088, today=today).basis == "year"
    lifetime = tracker.result_lifetime(22421, 9088, today=today)
    assert lifetime.basis == "lifetime"
    assert lifetime.value == pytest.approx(22421 / 9088, abs=0.01)
    assert (lifetime.energy_out, lifetime.energy_in) == (22421, 9088)
    assert lifetime.as_attributes()["underlag"] == "hela livslängden"


def test_the_lifetime_days_are_counted_from_the_commissioning_day(cop):
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_set_anchor(date(2025, 10, 10)))
    run(tracker.async_record(22400, 9100, today=date(2026, 9, 10)))
    assert tracker.result_lifetime(22499, 9116, today=date(2026, 10, 9)).days == 364
    # Without the anchor, from the oldest sample the tracker holds.
    plain = cop.CopTracker(FakeStore())
    run(plain.async_record(22400, 9100, today=date(2026, 9, 10)))
    assert plain.result_lifetime(22499, 9116, today=date(2026, 10, 9)).days == 29
    assert cop.CopTracker(FakeStore()).result_lifetime(22499, 9116).days == 0


def test_the_lifetime_without_counters_is_empty(cop):
    tracker = cop.CopTracker(FakeStore())
    assert tracker.result_lifetime(None, 9116) == cop.CopResult(None, "lifetime", 0)
    assert tracker.result_lifetime(22499, None) == cop.CopResult(None, "lifetime", 0)


# ---------------------------------- (2) the anchor leaves the rolling year


def test_the_rolling_year_does_not_blink_at_the_first_anniversary(cop):
    # VSH: commissioned 2025-10-10, the tracker's own samples from 2026-09-10.
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_set_anchor(date(2025, 10, 10)))
    run(tracker.async_record(22400, 9100, today=date(2026, 9, 10)))
    for today in (
        date(2026, 10, 9), date(2026, 10, 10), date(2026, 10, 24),
        date(2026, 10, 26), date(2027, 3, 1), date(2027, 9, 9),
    ):
        year = tracker.result_year(22900, 9300, today=today)
        assert year.basis == "year" and year.value is None, today
        assert year.reason and "av 365 dygn samlade" in year.reason, today
    # The tracker's own samples make a year once they are old enough.
    year = tracker.result_year(24000, 9700, today=date(2027, 9, 11))
    assert year.value == pytest.approx(1600 / 600, abs=0.01)
    assert year.days == 366


def test_the_first_year_still_takes_the_anchor(cop):
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_set_anchor(date(2025, 10, 10)))
    run(tracker.async_record(24000, 9700, today=date(2026, 10, 11)))
    first = tracker.result_first_year()
    assert first.basis == "first_year" and first.days == 366
    assert first.value == pytest.approx(24000 / 9700, abs=0.01)


# ------------------------------------------------- (3) the year's reason


def test_the_year_says_how_far_the_samples_have_come(cop):
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_record(22400, 9100, today=date(2026, 9, 10)))
    run(tracker.async_record(22600, 9180, today=date(2026, 10, 1)))
    year = tracker.result_year(22700, 9220, today=date(2026, 10, 9))
    assert year.days == 29
    assert year.reason == "29 av 365 dygn samlade, äldsta provet 2026-09-10"
    assert year.energy_out is None and year.energy_in is None
    assert year.as_attributes()["dygn i underlaget"] == 29


def test_a_year_without_any_sample_says_so(cop):
    year = cop.CopTracker(FakeStore()).result_year(22700, 9220, today=date(2026, 10, 9))
    assert year.days == 0
    assert year.reason == "0 av 365 dygn samlade, inget prov sparat ännu"


def test_a_gap_across_the_band_is_named_not_stretched_into_a_year(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(18000, 7500, today=today - timedelta(days=430)))
    run(tracker.async_record(21000, 8600, today=today - timedelta(days=100)))
    year = tracker.result_year(22421, 9088, today=today)
    assert year.value is None
    assert year.reason == "inget sparat prov är 365 till 380 dygn gammalt"
    assert year.days == 365


def test_counters_going_backwards_are_said_so_for_the_year(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(30000, 12000, today=today - timedelta(days=366)))
    year = tracker.result_year(22421, 9088, today=today)
    assert year.value is None
    assert year.reason == (
        f"räknarna har gått bakåt sedan {today - timedelta(days=366)}, "
        "enheten är bytt eller nollställd"
    )
    assert tracker.result(22421, 9088, today=today).basis == "lifetime"


def test_a_year_below_the_floor_leaves_the_pair_to_the_counters_reason(cop):
    tracker = cop.CopTracker(FakeStore())
    today = date(2026, 9, 9)
    run(tracker.async_record(22400, 9100, today=today - timedelta(days=366)))
    year = tracker.result_year(22409, 9104, today=today)
    assert year.value is None and year.reason is None
    assert (year.energy_out, year.energy_in) == (9.0, 4.0)
    assert "för lite energi ännu, 4.0 av 10 kWh" in cop.reason_for(year, 22409.0, 9104.0, 20000)


def test_unread_counters_give_the_year_no_progress_text(cop):
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_record(22400, 9100, today=date(2026, 9, 10)))
    year = tracker.result_year(None, None, today=date(2026, 10, 9))
    assert year == cop.CopResult(None, "year", 0)
    assert cop.reason_for(year, None, None) == "räknarna har inte lästs"


# -------------------------------------- (4) the first year needs the anchor


def test_the_first_year_without_a_commissioning_day_says_the_start_is_unknown(cop):
    # An i550 Pro: the history page is harvested, but its powered-on hours row
    # has no caption, so no anchor is ever set.
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_record(12000, 4000, today=date(2026, 10, 9)))
    first = tracker.result_first_year()
    assert first.value is None
    assert first.reason == "driftstarten är okänd"
    assert cop.reason_for(first, 12000.0, 4000.0, None) == "driftstarten är okänd"
    assert cop.reason_for(first, None, None) == "driftstarten är okänd"

    run(tracker.async_set_anchor(date(2025, 10, 10)))
    first = tracker.result_first_year()
    assert first.reason is None
    assert cop.reason_for(first, 12000.0, 4000.0, 8000) == "första året är inte fullt ännu"


def test_a_first_year_with_nothing_consumed_is_still_stuck(cop):
    tracker = cop.CopTracker(FakeStore())
    run(tracker.async_set_anchor(date(2025, 10, 10)))
    run(tracker.async_record(12000, 0, today=date(2026, 10, 11)))
    first = tracker.result_first_year()
    assert first.value is None and first.reason is None
    assert "står på noll" in cop.reason_for(first, 12000.0, 0.0, 20000)


# --------------------------------------------------- (5) a quiet display


def _web(success, read_at=None, **extra):
    return SimpleNamespace(last_update_success=success, read_at=read_at or {}, **extra)


def _at(hour, minute=0, second=0):
    return datetime(2026, 10, 9, hour, minute, second, tzinfo=timezone.utc)


def test_a_display_that_answers_gives_no_notice(cop):
    assert cop.display_silence(None) is None
    assert cop.display_silence(_web(True, {"k": _at(8)})) is None
    # Something without the flag at all, such as a runtime built for a test.
    assert cop.display_silence(SimpleNamespace(data={})) is None


def test_a_quiet_display_is_dated_by_its_last_read(cop):
    web = _web(False, {"a": _at(8), "b": _at(8, 31, 5), "c": "inte en tid"})
    assert cop.display_silence(web) == "displayen har inte svarat sedan 2026-10-09T08:31:05+00:00"


def test_a_quiet_display_that_never_answered_is_dated_from_the_start(cop):
    assert cop.display_silence(_web(False)) == (
        "displayen har inte svarat sedan Home Assistant startade"
    )


def test_the_coordinators_own_moment_wins_once_it_has_one(cop):
    # The coordinator's own attribute, by its real name and not by the
    # constant, so a constant that drifts from CtcWebCoordinator.last_harvest
    # fails here instead of falling back to read_at for good (F3.2, F8.3).
    # Anything but a datetime there is ignored, not trusted.
    assert cop.LAST_HARVEST_ATTRIBUTE == "last_harvest"
    web = _web(False, {"a": _at(8)}, last_harvest=_at(9))
    assert "2026-10-09T09:00:00" in cop.display_silence(web)
    web = _web(False, {"a": _at(8)}, last_harvest="nyss")
    assert "2026-10-09T08:00:00" in cop.display_silence(web)
    # The old, never matching name is read as nothing.
    web = _web(False, {"a": _at(8)}, last_harvest_at=_at(9))
    assert "2026-10-09T08:00:00" in cop.display_silence(web)


def test_a_quiet_display_comes_first_except_for_the_first_year(cop):
    notice = "displayen har inte svarat sedan 2026-10-09T08:00:00+00:00"
    year = cop.CopResult(2.8, "year", 366, 4421.0, 1588.0)
    assert cop.reason_for(year, 22421.0, 9088.0, 8000, display=notice) == notice
    empty = cop.CopResult(None, "lifetime", 0)
    assert cop.reason_for(empty, None, None, modbus_answered=False, display=notice) == notice
    # The first year is stored once and for all; the display cannot age it.
    first = cop.CopResult(2.47, "first_year", 366, 24000.0, 9700.0)
    assert cop.reason_for(first, 22421.0, 9088.0, 8000, display=notice) is None
    unfinished = cop.CopResult(None, "first_year", 0)
    assert cop.reason_for(unfinished, 22421.0, 9088.0, 8000, display=notice) == (
        "första året är inte fullt ännu"
    )


# ------------------------------------------- the order the reasons come in


def test_the_nearest_cause_is_given_first(cop):
    progress = cop.CopResult(None, "year", 29, reason="29 av 365 dygn samlade, äldsta provet 2026-09-10")
    # A register that never answered beats the tracker's progress text.
    assert "62341" in cop.reason_for(progress, 12000.0, None, modbus_answered=False)
    # With the counters read, the tracker's own account.
    assert cop.reason_for(progress, 12000.0, 4000.0) == progress.reason
    # A figure needs no reason.
    assert cop.reason_for(cop.CopResult(2.5, "year", 366, 1000.0, 400.0), 12000.0, 4000.0) is None
    # The counters' pair, last.
    still = cop.CopResult(None, "day", 1, 0.0, 0.0)
    assert "för lite energi ännu, 0.0 av 3 kWh" in cop.reason_for(still, 12000.0, 4000.0, 20000)
    stuck = cop.CopResult(None, "lifetime", 0, 12000.0, 0.0)
    assert "står på noll" in cop.reason_for(stuck, 12000.0, 0.0, 20000)
    odd = cop.CopResult(None, "lifetime", 0, 300.0, 9100.0)
    assert "orimlig" in cop.reason_for(odd, 300.0, 9100.0, 20000)


def test_a_day_without_a_sample_waits_for_one(cop):
    tracker = cop.CopTracker(FakeStore())
    now = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
    day = tracker.result_day(22440.0, 9112.0, now=now)
    assert day.reason == "väntar på ett prov som är 20 till 30 timmar gammalt"
    assert cop.reason_for(day, 22440.0, 9112.0) == day.reason
    # Counters that have not been read are the reason, not the missing sample.
    unread = tracker.result_day(None, None, now=now)
    assert unread == cop.CopResult(None, "day", 0)
    assert cop.reason_for(unread, None, None) == "räknarna har inte lästs"
    # A sample that is there but carries too little keeps the day's own text.
    run(tracker.async_record(22440.0, 9112.0, now=now - timedelta(hours=24)))
    quiet = tracker.result_day(22442.0, 9113.0, now=now)
    assert quiet.reason is None
    assert "för lite energi ännu, 1.0 av 3 kWh" in cop.reason_for(quiet, 22442.0, 9113.0, 20000)


# ------------------------------------------- (6) counter_fault gets the floor


def test_eleven_against_one_kilowatt_hour_is_no_fault(cop):
    # The display counts whole kilowatt hours, so one of them could be anything
    # from a half to one and a half: 11 against 1 is a quotient of nothing,
    # not a quotient of eleven. _ratio already refused to divide by it.
    assert cop.counter_fault(11.0, 1.0, 20000) is None
    assert cop._ratio(11.0, 1.0) is None
    # Above the floor the same quotient is the fault it looks like.
    assert cop.counter_fault(110.0, 10.0, 20000) == "implausible"
    assert cop.counter_fault(99.0, 9.0, 20000) is None
    # The two totals follow a fault to the report and nothing else, so a new
    # machine's first kilowatt hours stay at home.
    fault = cop.counter_fault(11.0, 1.0, 20000)
    assert (11.0 if fault else None) is None
    # And the sensor says too little, not wrong.
    assert "för lite energi ännu, 1.0 av 10 kWh" in cop.cop_reason(None, "lifetime", 11.0, 1.0, 20000)


def test_the_day_judges_its_pair_above_its_own_floor(cop):
    assert cop.counter_fault(60.0, 3.0, None, cop.MIN_CONSUMPTION_KWH_DAY) == "implausible"
    assert cop.counter_fault(20.0, 2.0, None, cop.MIN_CONSUMPTION_KWH_DAY) is None
    assert "orimlig" in cop.cop_reason(None, "day", 60.0, 3.0, 20000)
    assert "för lite energi" in cop.cop_reason(None, "day", 20.0, 2.0, 20000)


def test_a_stuck_counter_is_still_found_below_the_floor(cop):
    # A counter at zero on a running unit is judged on the hours, not the floor.
    assert cop.counter_fault(5.0, 0.0, 20000) == "stuck"
    assert cop.counter_fault(0.0, 5.0, 20000) == "stuck"


# ----------------------------------------------- the report asks by name


def test_the_report_takes_the_year_only_from_the_year(cop):
    # Against the real date, as the report asks: commissioned 400 days ago,
    # the anniversary's sample 34 days ago, which no passing year makes old
    # enough for the rolling year.
    tracker = cop.CopTracker(FakeStore())
    commissioned = date.today() - timedelta(days=400)
    run(tracker.async_set_anchor(commissioned))
    run(tracker.async_record(24000, 9700, today=commissioned + timedelta(days=366)))
    runtime = SimpleNamespace(
        cop=tracker,
        web=SimpleNamespace(data={"out": 24000.0, "in": 9700.0}),
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in"),
    )
    figures = cop.cop_for_report(runtime)
    assert figures["cop_year"] is None
    assert figures["cop_first_year"] == pytest.approx(24000 / 9700, abs=0.01)
    assert figures["cop_lifetime"] == pytest.approx(24000 / 9700, abs=0.01)
