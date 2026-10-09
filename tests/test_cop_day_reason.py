"""The daily figure's reason is about the day, not about the lifetime counters.

For the span of a day the sensor hands cop_reason the day's two deltas together
with the unit's lifetime powered-on hours, and counter_fault judged the deltas
by the lifetime rule: a unit switched on for a day with a delta of zero was "a
counter standing at zero although the unit has been running". A mild summer day
without a compressor start got that text instead of "too little energy so far",
and a day of 0 against 1 was "an impossible quotient". The deltas are not the
totals, so for a day the stuck test is left out and below the floor nothing is
judged at all; above it the test for an impossible pair, which is about the
pair itself, stays. The first year is the other way round: only no sample at
all means it is unfinished, a sample with nothing consumed in it is a counter
that stood still.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def test_a_day_without_compressor_running_is_too_little_energy_not_stuck(cop):
    # Two samples 24 hours apart with the counters unchanged, on a unit that
    # has been switched on for years, exactly as the sensor composes it.
    tracker = cop.CopTracker(FakeStore())
    then = datetime(2026, 7, 14, 6, 0, tzinfo=timezone.utc)
    asyncio.run(tracker.async_record(12000.0, 4000.0, now=then))
    result = tracker.result_day(12000.0, 4000.0, now=then + timedelta(hours=24))
    assert result == cop.CopResult(None, "day", 1, 0.0, 0.0)
    reason = cop.cop_reason(None, "day", result.energy_out, result.energy_in, 20000)
    assert "för lite energi" in reason and "0.0 av 3 kWh" in reason
    assert "står på noll" not in reason


def test_a_kilowatt_hour_of_rounding_over_a_day_is_neither_stuck_nor_impossible(cop):
    # The display counts whole kilowatt hours and rounds the two counters
    # independently, so 2 against 0 over a day is more common than 0 against 0,
    # and 0 against 1 is a quotient of nothing, not an impossible one.
    for out, consumed in ((2.0, 0.0), (0.0, 1.0), (1.0, 0.0), (0.0, 2.0)):
        reason = cop.cop_reason(None, "day", out, consumed, 20000)
        assert "för lite energi" in reason, (out, consumed, reason)
        assert f"{consumed:.1f} av 3 kWh" in reason


def test_a_day_whose_pair_is_impossible_is_still_said_so(cop):
    assert "orimlig" in cop.cop_reason(None, "day", 60.0, 3.0, 20000)


def test_the_lifetime_and_yearly_stuck_tests_are_untouched(cop):
    assert "står på noll" in cop.cop_reason(None, "lifetime", 12000.0, 0.0, 20000)
    assert "står på noll" in cop.cop_reason(None, "year", 0.0, 0.0, 20000)


def test_a_first_year_with_nothing_consumed_is_stuck_not_unfinished(cop):
    # An i360 commissioned about a year ago whose 62341 never moved: the first
    # year has its sample, with nothing consumed in it.
    assert "första året" in cop.cop_reason(None, "first_year", None, None)
    assert "står på noll" in cop.cop_reason(None, "first_year", 12000.0, 0.0, 20000)
