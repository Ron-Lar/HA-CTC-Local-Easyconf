"""The coefficient of performance against a counter row that stops being read (F3.1, F3.2, F4.1).

Since R5 a page the harvest cannot reach is no failure of the harvest: the
other pages are read, the coordinator reports success, and only that page's
rows age in the data until their own sensors go unavailable. The counters
behind the coefficient of performance are such rows, and so is the display's
starts per day behind the mean run. Here the sensors, the notice and the
sampling are held to the coordinator's own freshness rule, key by key: a
stale counter row gives the notice, named and dated with its last read; the
day goes unavailable with it; a sample is taken only off a fresh reading and a
reading is kept once; and the mean run goes empty when the starts are stale.
The quiet display's notice reads the coordinator's own attribute, by the name
CtcWebCoordinator actually has.

The web coordinator is stood in for by a double that carries the real one's
freshness rule, since the sensors read it through ``is_fresh`` and
``last_read``; the real coordinator is built under the stand-ins where a test
needs the class itself.
"""

from __future__ import annotations

import asyncio
import pathlib
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
#: Three intervals of half an hour, the coordinator's own patience.
STALE_AFTER = timedelta(minutes=90)

HEAT = SimpleNamespace(key="p25_avgiven_varme_totalt", label="Avgiven värme totalt")
CONSUMED = SimpleNamespace(key="p25_tillford_energi_totalt", label="Tillförd energi totalt")
STARTS = SimpleNamespace(key="p25_antal_starter_24", label="Antal starter /24")


def run(coro):
    return asyncio.run(coro)


def _at(hour, minute=0):
    return datetime(2026, 10, 9, hour, minute, tzinfo=timezone.utc)


class FakeWeb:
    """A web coordinator as the sensors see it: the data, when each key was
    read, and the freshness rule CtcWebCoordinator applies to them."""

    def __init__(self, data, read_at, *, success=True, now=NOW, **extra):
        self.data = data
        self.read_at = read_at
        self.last_update_success = success
        self.now = now
        for name, value in extra.items():
            setattr(self, name, value)

    def last_read(self, key):
        return self.read_at.get(key)

    def is_fresh(self, key, now=None):
        if key not in (self.data or {}):
            return False
        read = self.read_at.get(key)
        return read is not None and (now or self.now) - read <= STALE_AFTER


def _web(*, heat_read=None, consumed_read=None, starts_read=None, success=True, **extra):
    """Both counters and the starts in the data, each read at the moment given,
    and left out of the data where no moment is given."""
    data, read_at = {}, {}
    for row, read in ((HEAT, heat_read), (CONSUMED, consumed_read), (STARTS, starts_read)):
        if read is not None:
            data[row.key] = {HEAT.key: 22499.0, CONSUMED.key: 9116.0, STARTS.key: 8.0}[row.key]
            read_at[row.key] = read
    return FakeWeb(data, read_at, success=success, **extra)


FRESH = NOW - timedelta(minutes=20)
STALE = NOW - timedelta(hours=2)


# ------------------------------------------------------------- the notice


def test_a_stale_counter_row_gives_the_notice_named_and_dated(cop):
    web = _web(heat_read=STALE, consumed_read=STALE)
    assert cop.display_silence(web, HEAT, CONSUMED) == (
        "displayens rad Avgiven värme totalt har inte lästs sedan 2026-10-09T10:00:00+00:00"
    )
    # The first stale row names the notice; the consumed row alone does too.
    web = _web(heat_read=FRESH, consumed_read=STALE)
    assert cop.display_silence(web, HEAT, CONSUMED) == (
        "displayens rad Tillförd energi totalt har inte lästs sedan 2026-10-09T10:00:00+00:00"
    )
    # Without a label the key names it; a row of None is skipped.
    web = _web(heat_read=STALE, consumed_read=STALE)
    bare = SimpleNamespace(key=HEAT.key)
    assert "p25_avgiven_varme_totalt har inte lästs" in cop.display_silence(web, None, bare, CONSUMED)


def test_a_fresh_or_unread_row_gives_no_notice(cop):
    assert cop.display_silence(_web(heat_read=FRESH, consumed_read=FRESH), HEAT, CONSUMED) is None
    # Never read: not stale but unread, and the sensors say so in other words.
    assert cop.display_silence(_web(), HEAT, CONSUMED) is None
    # A double without the rule, as the span tests build, cannot be stale.
    plain = SimpleNamespace(data={HEAT.key: 1.0}, last_update_success=True, read_at={HEAT.key: STALE})
    assert cop.display_silence(plain, HEAT) is None
    assert cop.display_silence(None, HEAT) is None
    # No rows asked about: the old behaviour, nothing while the display answers.
    assert cop.display_silence(_web(heat_read=STALE)) is None


def test_a_quiet_display_comes_before_the_rows_and_is_dated_by_the_harvest(cop):
    web = _web(heat_read=STALE, consumed_read=STALE, success=False, last_harvest=_at(11, 5))
    assert cop.display_silence(web, HEAT, CONSUMED) == (
        "displayen har inte svarat sedan 2026-10-09T11:05:00+00:00"
    )


def test_stale_rows_are_the_rows_read_once_and_stale_since(cop):
    web = _web(heat_read=STALE, consumed_read=FRESH)
    assert cop.stale_rows(web, [HEAT, CONSUMED, STARTS, None]) == [(HEAT, STALE)]
    assert cop.stale_rows(_web(), [HEAT, CONSUMED]) == []
    assert cop.row_read_at(web, HEAT.key) == STALE
    assert cop.row_read_at(web, STARTS.key) is None
    assert cop.row_is_fresh(web, HEAT.key) is False
    assert cop.row_is_fresh(web, CONSUMED.key) is True
    assert cop.row_is_fresh(SimpleNamespace(), HEAT.key) is None
    assert cop.row_read_at(SimpleNamespace(), HEAT.key) is None


# ---------------------------------------------------------- the sampling


def _runtime_for_sampling(web, *, consumed_on_display=True):
    return SimpleNamespace(
        web=web,
        energy_out=HEAT,
        energy_in=CONSUMED if consumed_on_display else None,
        consumption_snapshot=None if consumed_on_display else SimpleNamespace(value=9100.0),
    )


def test_the_pair_is_read_at_the_heat_rows_moment_only_while_both_are_fresh(cop):
    assert cop.counter_rows(_runtime_for_sampling(None)) == [HEAT, CONSUMED]
    assert cop.counter_rows(_runtime_for_sampling(None, consumed_on_display=False)) == [HEAT]
    later = FRESH + timedelta(seconds=3)
    both = _runtime_for_sampling(_web(heat_read=FRESH, consumed_read=later))
    assert cop.counters_read_at(both) == FRESH
    # One of the two stale, or never read: no moment, so no sample.
    assert cop.counters_read_at(_runtime_for_sampling(_web(heat_read=FRESH, consumed_read=STALE))) is None
    assert cop.counters_read_at(_runtime_for_sampling(_web(heat_read=STALE, consumed_read=FRESH))) is None
    assert cop.counters_read_at(_runtime_for_sampling(_web(heat_read=FRESH))) is None
    assert cop.counters_read_at(_runtime_for_sampling(_web())) is None
    assert cop.counters_read_at(_runtime_for_sampling(None)) is None
    # On the Modbus route only the heat row is on the display.
    modbus = _runtime_for_sampling(_web(heat_read=FRESH), consumed_on_display=False)
    assert cop.counters_read_at(modbus) == FRESH
    modbus = _runtime_for_sampling(_web(heat_read=STALE), consumed_on_display=False)
    assert cop.counters_read_at(modbus) is None


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def test_the_tracker_keeps_a_reading_once(cop):
    store = FakeStore()
    tracker = cop.CopTracker(store)
    assert tracker.last_sample_at is None
    read = _at(6)
    run(tracker.async_record(22400.0, 9100.0, now=read))
    assert tracker.last_sample_at == read
    # The same moment again, as the six hour timer brings it while the page
    # is not reached: dropped, in the run and in the day's map.
    run(tracker.async_record(22400.0, 9100.0, now=read))
    run(tracker.async_record(22450.0, 9120.0, now=read))
    assert len(store.data["recent"]) == 1
    assert store.data["samples"] == {"2026-10-09": [22400.0, 9100.0]}
    # A moment older than the newest is no new reading either.
    run(tracker.async_record(22300.0, 9050.0, now=read - timedelta(hours=6)))
    assert len(store.data["recent"]) == 1
    # A newer one is.
    run(tracker.async_record(22440.0, 9112.0, now=read + timedelta(hours=6)))
    assert len(store.data["recent"]) == 2
    assert tracker.last_sample_at == read + timedelta(hours=6)
    # Across a restart the store remembers the moment, so the pair that comes
    # back out of the display store is not written a second time.
    revived = cop.CopTracker(store)
    run(revived.async_record(22440.0, 9112.0, now=read + timedelta(hours=6)))
    assert len(store.data["recent"]) == 2
    # The day's figure reads the newest sample old enough, as ever: the one
    # from noon, 24 hours before.
    assert revived.result_day(22480.0, 9124.0, now=read + timedelta(hours=30)).value == pytest.approx(
        40 / 12, abs=0.01
    )


def test_a_reading_without_a_moment_is_now_and_always_new(cop):
    # Callers that hand in no moment, the tests among them, record as before.
    store = FakeStore()
    tracker = cop.CopTracker(store)
    run(tracker.async_record(22400.0, 9100.0, today=date(2026, 10, 8)))
    run(tracker.async_record(22440.0, 9112.0, today=date(2026, 10, 9)))
    assert len(store.data["recent"]) == 2
    assert len(store.data["samples"]) == 2


def test_the_set_up_samples_off_the_counters_read_moment_and_never_the_clock():
    setup = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    setup = setup.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    record = setup.split("async def _record_cop")[1].split("await _record_cop(")[0]
    assert "read_at = counters_read_at(runtime)" in record
    assert "if read_at is not None:" in record
    assert "now=read_at" in record
    assert "datetime.now" not in record and "utcnow" not in record
    # The stored pair still comes in with its own moment at start-up.
    assert "_record_cop(read_at=runtime.web.last_read(runtime.energy_out.key))" in setup


# ------------------------------------------------------------ the sensors


@pytest.fixture(scope="module")
def sensor():
    ha_stub.skip_unless_stubbed()
    return load("sensor")


def _runtime(cop, web, *, starts=False):
    tracker = cop.CopTracker(FakeStore())
    # The sensors ask the tracker for the day against the clock, so the
    # sample that serves as yesterday is laid 24 hours before the clock;
    # the rows' freshness is judged by the double against NOW, which is
    # a separate matter.
    run(tracker.async_record(22400.0, 9100.0, now=datetime.now(timezone.utc) - timedelta(hours=24)))
    return SimpleNamespace(
        cop=tracker,
        web=web,
        modbus=SimpleNamespace(answered={62341}, descriptions=[], data={"compressor_hours_24h": 240}),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
        energy_out=HEAT,
        energy_in=CONSUMED,
        consumption_snapshot=None,
        alarms=None,
        seen=None,
        transitions=None,
        starts_per_day=STARTS if starts else None,
        operating_hours=None,
        pages=[],
        identity=SimpleNamespace(
            heatpump_model=None, display_firmware=None, heatpump_firmware=None,
            bootloader=None, serial=None, manufactured=None,
        ),
    )


def _cop_sensors(sensor, runtime):
    added = []
    entry = SimpleNamespace(runtime_data=runtime, entry_id="test", options={}, async_on_unload=lambda f: None)
    run(sensor.async_setup_entry(None, entry, added.extend))
    return {s._span: s for s in added if isinstance(s, sensor.CtcCopSensor)}


def test_fresh_counters_leave_every_span_standing_as_before(sensor, cop):
    sensors = _cop_sensors(sensor, _runtime(cop, _web(heat_read=FRESH, consumed_read=FRESH)))
    assert all(one.available for one in sensors.values())
    assert sensors["day"].native_value == pytest.approx(99 / 16, abs=0.01)
    assert sensors["lifetime"].native_value == pytest.approx(22499 / 9116, abs=0.01)
    assert "skäl" not in sensors["day"].extra_state_attributes


def test_a_stale_counter_row_takes_the_day_and_gives_the_rest_the_notice(sensor, cop):
    sensors = _cop_sensors(sensor, _runtime(cop, _web(heat_read=STALE, consumed_read=STALE)))
    notice = "displayens rad Avgiven värme totalt har inte lästs sedan 2026-10-09T10:00:00+00:00"
    assert sensors["day"].available is False
    for span in ("week", "month", "year", "lifetime"):
        one = sensors[span]
        assert one.available is True, span
        assert one.native_value is None, span
        assert one.extra_state_attributes["skäl"] == notice, span
    # The first year is stored once and for all: no notice there.
    first = sensors["first_year"]
    assert first.available is True
    assert first.extra_state_attributes["skäl"] == "driftstarten är okänd"


def test_counters_never_read_leave_the_day_standing_with_its_own_reason(sensor, cop):
    sensors = _cop_sensors(sensor, _runtime(cop, _web()))
    day = sensors["day"]
    assert day.available is True
    assert day.native_value is None
    assert day.extra_state_attributes["skäl"] == "räknarna har inte lästs"
    assert sensors["lifetime"].extra_state_attributes["skäl"] == "räknarna har inte lästs"


def test_a_quiet_display_is_dated_by_the_coordinators_last_harvest(sensor, cop):
    web = _web(heat_read=FRESH, consumed_read=FRESH, success=False, last_harvest=_at(11, 5))
    sensors = _cop_sensors(sensor, _runtime(cop, web))
    assert sensors["day"].available is False
    assert sensors["year"].extra_state_attributes["skäl"] == (
        "displayen har inte svarat sedan 2026-10-09T11:05:00+00:00"
    )


# ----------------------------------------------------------- the mean run


def _mean_run(sensor, cop, web):
    runtime = _runtime(cop, web, starts=True)
    return sensor.CtcMeanRunSensor(runtime)


def test_the_mean_run_divides_by_fresh_starts_and_says_when_they_were_read(sensor, cop):
    mean = _mean_run(sensor, cop, _web(starts_read=FRESH))
    assert mean.native_value == 30.0
    assert mean.extra_state_attributes == {
        "kompressordrift senaste dygnet": 240,
        "antal starter /24 h": 8.0,
        "antal starter senast läst": "2026-10-09T11:40:00+00:00",
    }


def test_the_mean_run_goes_empty_when_the_starts_are_stale_or_the_display_quiet(sensor, cop):
    stale = _mean_run(sensor, cop, _web(starts_read=STALE))
    assert stale.native_value is None
    assert stale.extra_state_attributes == {
        "kompressordrift senaste dygnet": 240,
        "antal starter /24 h": None,
        "antal starter senast läst": "2026-10-09T10:00:00+00:00",
    }
    quiet = _mean_run(sensor, cop, _web(starts_read=FRESH, success=False))
    assert quiet.native_value is None
    assert quiet.extra_state_attributes["antal starter /24 h"] is None
    never = _mean_run(sensor, cop, _web())
    assert never.native_value is None
    assert never.extra_state_attributes["antal starter senast läst"] is None


# ------------------------------------------- the coordinator's own attribute


@pytest.fixture(scope="module")
def coordinator():
    ha_stub.skip_unless_stubbed()
    return load("coordinator")


def test_the_notice_reads_the_attribute_the_coordinator_actually_has(coordinator, cop):
    # Built from the real class, so a renamed field fails here: the name is
    # read without a default (F3.2, F8.3).
    web = coordinator.CtcWebCoordinator(
        hass=object(), client=SimpleNamespace(panel=None), pages=[], interval=1800
    )
    assert getattr(web, cop.LAST_HARVEST_ATTRIBUTE) is None
    # Moments the clock has long passed, so the coordinator's own rule, which
    # judges against the clock, calls the row stale whenever this runs.
    read = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    harvested = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    web.last_update_success = False
    web.read_at = {"a": read}
    web.last_harvest = harvested
    assert cop.display_silence(web) == "displayen har inte svarat sedan 2026-01-01T09:00:00+00:00"
    # And the freshness rule the sensors read through is the coordinator's.
    web.last_update_success = True
    web.data = {"a": 1.0}
    row = SimpleNamespace(key="a", label="A")
    assert cop.row_read_at(web, "a") == read
    assert cop.row_is_fresh(web, "a") is False
    assert cop.stale_rows(web, [row]) == [(row, read)]
    assert cop.display_silence(web, row) == "displayens rad A har inte lästs sedan 2026-01-01T08:00:00+00:00"
