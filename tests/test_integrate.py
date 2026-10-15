"""Energy of the integration's own making, where the counters never move.

The fleet's one installation with a 700 series outdoor unit fills both lifetime
counters; the 400 series and the two in the 600 series leave them at a clean
zero, behind two different indoor models. The two powers on the heat pump's page
are what is left to work with, and these tests hold the sums made of them: the
arithmetic, what a gap does, and the rule that the quotient never takes a
counter for one side and a sum for the other.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from conftest import load


class FakeStore:
    def __init__(self) -> None:
        self.data = None
        self.delayed = 0

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data

    def async_delay_save(self, maker, _delay):
        self.delayed += 1
        self.data = maker()


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture(scope="module")
def integrate():
    return load("integrate")


def _at(minutes: int) -> datetime:
    return datetime(2026, 10, 15, 6, 0, tzinfo=UTC) + timedelta(minutes=minutes)


# ------------------------------------------------------------ the arithmetic


def test_the_first_reading_only_sets_the_baseline(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    assert adder.note(_at(0), 4.0, 1.0) is False
    assert adder.totals == (None, None)


def test_power_over_time_is_energy(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    adder.note(_at(0), 4.0, 1.0)
    # Half an hour at the mean of 4 and 6 kW is 2.5 kWh out, and at the mean of
    # 1 and 2 kW is 0.75 kWh in. The trapezoid, not the latest reading.
    assert adder.note(_at(30), 6.0, 2.0) is True
    assert adder.totals == (2.5, 0.8)
    assert adder.hours == 0.5


def test_a_compressor_that_was_off_adds_nothing_but_time(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    adder.note(_at(0), 0.0, 0.0)
    adder.note(_at(30), 0.0, 0.0)
    assert adder.totals == (0.0, 0.0)
    assert adder.hours == 0.5


def test_a_gap_too_long_to_account_for_starts_again_from_itself(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    adder.note(_at(0), 6.0, 2.0)
    # Three hours of silence: nobody knows what the pump did, so nothing is
    # added rather than three hours of the last reading invented.
    assert adder.note(_at(180), 6.0, 2.0) is False
    assert adder.totals == (None, None)
    # And the sample that was too late is the baseline for the next one.
    assert adder.note(_at(210), 6.0, 2.0) is True
    assert adder.totals == (3.0, 1.0)


def test_the_same_harvest_twice_and_a_clock_that_went_back_add_nothing(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    adder.note(_at(30), 6.0, 2.0)
    assert adder.note(_at(30), 6.0, 2.0) is False
    assert adder.note(_at(0), 6.0, 2.0) is False
    assert adder.totals == (None, None)


def test_a_reading_that_is_not_a_number_is_no_reading(integrate):
    adder = integrate.PowerIntegrator(FakeStore())
    adder.note(_at(0), 4.0, 1.0)
    assert adder.note(_at(30), None, 1.0) is False
    assert adder.note(_at(30), "4,0", 1.0) is False
    assert adder.note(None, 4.0, 1.0) is False
    assert adder.totals == (None, None)


def test_the_sums_survive_a_restart(integrate):
    store = FakeStore()
    adder = integrate.PowerIntegrator(store)
    run(adder.async_load())
    adder.note(_at(0), 4.0, 1.0)
    adder.note(_at(30), 6.0, 2.0)
    adder.save_soon()
    assert store.delayed == 1

    again = integrate.PowerIntegrator(store)
    run(again.async_load())
    assert again.totals == (2.5, 0.8)
    assert again.hours == 0.5
    # The last reading came back too, so the next half hour follows on.
    assert again.note(_at(60), 6.0, 2.0) is True
    assert again.totals == (5.5, 1.8)


# -------------------------------------------------------------- the two rows


def _power(const, label, key, unit="kW"):
    return const.SlowValue(key=key, label=label, page=22, screen=118,
                           fmt="%.1f", var_indices=[1], unit=unit)


def test_the_two_power_rows_are_found_by_what_the_display_printed(cop, const):
    page = const.SlowPage(page=22, title="Värmepump", screens=[118])
    page.values = [
        _power(const, "Inverter Motoreffekt", "motor"),
        _power(const, "Avgiven värme", "out"),
        _power(const, "Avgiven kyla", "cold"),
        _power(const, "Tillförd effekt", "in"),
    ]
    out, consumed = cop.find_power_pair([page])
    assert (out.key, consumed.key) == ("out", "in")


def test_a_counter_and_a_period_are_not_a_power(cop, const):
    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values = [
        _power(const, "Avgiven värme totalt", "total", unit="kWh"),
        _power(const, "Avgiven värme/30 dagar", "period"),
    ]
    assert cop.find_power_pair([page]) == (None, None)


def test_the_english_panel_and_the_older_swedish_name_are_read_too(cop, const):
    english = const.SlowPage(page=22, title="Heat pump", screens=[118])
    english.values = [_power(const, "Energy output", "out"), _power(const, "Energy add", "in")]
    assert [v.key for v in cop.find_power_pair([english])] == ["out", "in"]
    older = const.SlowPage(page=22, title="Värmepump", screens=[118])
    older.values = [_power(const, "Avgiven energi", "out"), _power(const, "Tillförd effekt", "in")]
    assert [v.key for v in cop.find_power_pair([older])] == ["out", "in"]


# ------------------------------------------------- one source, never a mixture


def _runtime(cop, integrate, counters, integrated):
    adder = integrate.PowerIntegrator(FakeStore())
    if integrated is not None:
        adder.note(_at(0), 0.0, 0.0)
        adder.note(_at(60), integrated[0] * 2, integrated[1] * 2)
    return SimpleNamespace(
        web=SimpleNamespace(data={"out": counters[0], "in": counters[1]}),
        energy_out=SimpleNamespace(key="out", label="Avgiven värme totalt"),
        energy_in=SimpleNamespace(key="in", label="Tillförd energi totalt"),
        consumption_snapshot=None,
        integrator=adder,
    )


def test_the_machines_own_counters_come_first(cop, integrate):
    runtime = _runtime(cop, integrate, (22499.0, 9116.0), (500.0, 200.0))
    assert cop.current_totals(runtime) == (22499.0, 9116.0)
    assert cop.totals_source(runtime) == ("displayen", "displayen")


def test_counters_at_zero_hand_over_to_the_sums(cop, integrate):
    runtime = _runtime(cop, integrate, (0.0, 0.0), (500.0, 200.0))
    assert cop.current_totals(runtime) == (500.0, 200.0)
    assert cop.CopTracker(FakeStore()).result_lifetime(*cop.current_totals(runtime)).value == 2.5
    out, consumed = cop.totals_source(runtime)
    assert out == consumed == "egen summering av displayens momentana effekt"


def test_one_counter_at_zero_takes_the_sums_for_both_sides(cop, integrate):
    # Never a counter for one side and a sum for the other: that would divide
    # two different spans by each other.
    runtime = _runtime(cop, integrate, (22499.0, 0.0), (500.0, 200.0))
    assert cop.current_totals(runtime) == (500.0, 200.0)


def test_without_sums_of_its_own_the_zeroes_stand_as_they_are(cop, integrate):
    runtime = _runtime(cop, integrate, (0.0, 0.0), None)
    assert cop.current_totals(runtime) == (0.0, 0.0)
    assert cop.cop_reason(None, "lifetime", 0.0, 0.0, 20000) is not None


# ------------------------------------- a counter at zero against the compressor's hours


def test_zero_counters_are_a_fault_only_where_the_compressor_has_run(cop):
    # Portugal: the unit has stood powered on for 16697 hours, the display counts
    # no compressor hours at all, and Modbus 62214 says the compressor has run
    # 4160. Judged on the unit's hours this was called a controller at fault even
    # where the compressor had never run; judged on the compressor's it is one
    # only where it has.
    assert cop.counter_fault(0.0, 0.0, 4160) == "stuck"
    assert cop.counter_fault(0.0, 0.0, 3) is None
    assert cop.counter_fault(0.0, 0.0, 0) is None
    reason = cop.cop_reason(None, "lifetime", 0.0, 0.0, 4160)
    assert "kompressorn har gått 4160 timmar" in reason


def test_the_compressor_hours_come_from_modbus_not_from_the_display(cop):
    runtime = SimpleNamespace(modbus=SimpleNamespace(data={"compressor_hours": 4160.0}))
    assert cop.compressor_hours(runtime) == 4160.0
    assert cop.compressor_hours(SimpleNamespace(modbus=SimpleNamespace(data={}))) is None
    assert cop.compressor_hours(SimpleNamespace()) is None


# ------------------------------------------ rows that are there and give nothing


def test_the_pair_is_read_or_it_is_not(cop):
    rows = {"power_out": SimpleNamespace(key="out"), "power_in": SimpleNamespace(key="in")}
    both = SimpleNamespace(web=SimpleNamespace(data={"out": 8.3, "in": 3.0}), **rows)
    assert cop.power_now(both) == (8.3, 3.0)
    # A heat pump switched off: the rows are on the page, the page answers nothing.
    off = SimpleNamespace(web=SimpleNamespace(data={}), **rows)
    assert cop.power_now(off) is None
    half = SimpleNamespace(web=SimpleNamespace(data={"out": 8.3}), **rows)
    assert cop.power_now(half) is None
    without = SimpleNamespace(web=SimpleNamespace(data={"out": 8.3, "in": 3.0}),
                              power_out=None, power_in=None)
    assert cop.power_now(without) is None


def test_an_empty_sum_says_which_of_the_two_it_is(cop, integrate):
    assert cop.POWER_SILENT != cop.POWER_FIRST_READ
    assert "avstängd" in cop.POWER_SILENT
    assert "nästa skörd" in cop.POWER_FIRST_READ
