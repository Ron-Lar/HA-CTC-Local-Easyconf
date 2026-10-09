"""The coefficient of performance sensors, span by span, against a fake runtime.

The sensor platform is built here the way the set-up builds it, with a runtime
that holds a tracker, the display's data and a web coordinator, so what each
sensor shows and says can be checked in both worlds: under the stand-ins when
Home Assistant is not installed, and against the real entity classes when it
is. Nothing here needs a running core, since the properties read the runtime.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load


@pytest.fixture(scope="module")
def sensor():
    ha_stub.skip_unless_stubbed()
    return load("sensor")


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def run(coro):
    return asyncio.run(coro)


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)

#: The sensors ask the tracker for today, so the samples are laid out against
#: the real date: commissioned 500 days ago, the tracker's own samples from
#: 29 days ago, which is 471 days after the start and so outside the first
#: year's band, and an anniversary sample 134 days ago where a test wants
#: one. Nothing here drifts into the rolling year's band as time passes.
TODAY = date.today()
COMMISSIONED = TODAY - timedelta(days=500)
OLDEST = TODAY - timedelta(days=29)
ANNIVERSARY = COMMISSIONED + timedelta(days=366)


def _runtime(cop, *, hours_row=True, anchor=COMMISSIONED, data=None, success=True):
    """An i255 whose history page is harvested: both counters on the display."""
    tracker = cop.CopTracker(FakeStore())
    if anchor is not None:
        run(tracker.async_set_anchor(anchor))
    run(tracker.async_record(22400.0, 9100.0, today=OLDEST))
    if data is None:
        data = {"out": 22499.0, "in": 9116.0, "hours": 8044.0}
    web = SimpleNamespace(
        data=data,
        last_update_success=success,
        read_at={key: NOW - timedelta(minutes=20) for key in data},
    )
    return SimpleNamespace(
        cop=tracker,
        web=web,
        modbus=SimpleNamespace(answered={62341}, descriptions=[]),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in"),
        consumption_snapshot=None,
        alarms=None,
        seen=None,
        transitions=None,
        starts_per_day=None,
        operating_hours=[SimpleNamespace(key="hours")] if hours_row else None,
        pages=[],
        identity=SimpleNamespace(
            heatpump_model=None, display_firmware=None, heatpump_firmware=None,
            bootloader=None, serial=None, manufactured=None,
        ),
    )


def _sensors(sensor, runtime):
    added = []
    run(sensor.async_setup_entry(None, SimpleNamespace(runtime_data=runtime, entry_id="test", options={}, async_on_unload=lambda f: None), added.extend))
    return {s._span: s for s in added if isinstance(s, sensor.CtcCopSensor)}


# ------------------------------------------------------------ which exist


def test_every_span_gets_a_sensor_whatever_the_history_page_carries(sensor, cop):
    with_hours = _sensors(sensor, _runtime(cop))
    without = _sensors(sensor, _runtime(cop, hours_row=False, anchor=None))
    assert set(with_hours) == set(without)
    assert {"day", "year", "first_year", "lifetime"} <= set(with_hours)
    assert with_hours["year"]._attr_unique_id == "ctc_ecozenith_pump_cop_year"
    assert without["first_year"].extra_state_attributes["skäl"] == "driftstarten är okänd"


# --------------------------------------------------- what each span shows


def test_each_sensor_answers_under_its_own_span(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop))
    day = sensors["day"]
    assert day.native_value is None
    assert day.extra_state_attributes["underlag"] == "senaste dygnet"
    assert day.extra_state_attributes["skäl"] == "väntar på ett prov som är 20 till 30 timmar gammalt"
    year = sensors["year"]
    assert year.native_value is None
    assert year.extra_state_attributes["underlag"] == "rullande år"
    assert year.extra_state_attributes["dygn i underlaget"] == 29
    assert year.extra_state_attributes["skäl"] == f"29 av 365 dygn samlade, äldsta provet {OLDEST}"
    first = sensors["first_year"]
    assert first.native_value is None
    assert first.extra_state_attributes["skäl"] == "första året är inte fullt ännu"
    lifetime = sensors["lifetime"]
    assert lifetime.native_value == pytest.approx(22499 / 9116, abs=0.01)
    assert lifetime.extra_state_attributes["underlag"] == "hela livslängden"
    assert lifetime.extra_state_attributes["dygn i underlaget"] == 500
    assert "skäl" not in lifetime.extra_state_attributes
    for one in sensors.values():
        assert one.extra_state_attributes["tillförd energi ur"] == "displayen"


def test_the_first_year_carries_the_anniversary_and_the_year_does_not(sensor, cop):
    runtime = _runtime(cop)
    run(runtime.cop.async_record(24000.0, 9700.0, today=ANNIVERSARY))
    sensors = _sensors(sensor, runtime)
    assert sensors["first_year"].native_value == pytest.approx(24000 / 9700, abs=0.01)
    assert sensors["first_year"].extra_state_attributes["dygn i underlaget"] == 366
    assert sensors["year"].native_value is None
    assert "av 365 dygn samlade" in sensors["year"].extra_state_attributes["skäl"]


# ------------------------------------------------------ a quiet display


def test_a_quiet_display_takes_the_day_and_leaves_the_rest_with_a_notice(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop, success=False))
    notice = "displayen har inte svarat sedan 2026-10-09T11:40:00+00:00"
    assert sensors["day"].available is False
    for span in ("year", "lifetime"):
        one = sensors[span]
        assert one.available is True, span
        assert one.native_value is None, span
        assert one.extra_state_attributes["skäl"] == notice, span
        assert one.extra_state_attributes["underlag"], span
    # The first year is stored, so the display cannot age it: no notice there.
    first = sensors["first_year"]
    assert first.available is True
    assert first.extra_state_attributes["skäl"] == "första året är inte fullt ännu"


def test_a_quiet_display_leaves_a_stored_first_year_standing(sensor, cop):
    runtime = _runtime(cop, success=False)
    run(runtime.cop.async_record(24000.0, 9700.0, today=ANNIVERSARY))
    first = _sensors(sensor, runtime)["first_year"]
    assert first.native_value == pytest.approx(24000 / 9700, abs=0.01)
    assert "skäl" not in first.extra_state_attributes


def test_the_sensors_stay_available_while_the_display_answers(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop))
    assert all(one.available for one in sensors.values())


# -------------------------------------------------- the counters' reasons


def test_unread_counters_are_said_so_on_every_span_but_the_first_year(sensor, cop):
    sensors = _sensors(sensor, _runtime(cop, data={"hours": 8044.0}))
    for span in ("day", "year", "lifetime"):
        assert sensors[span].native_value is None, span
        assert sensors[span].extra_state_attributes["skäl"] == "räknarna har inte lästs", span
    assert sensors["first_year"].extra_state_attributes["skäl"] == "första året är inte fullt ännu"


def test_a_register_that_never_answered_is_the_reason_on_the_modbus_route(sensor, cop):
    runtime = _runtime(cop, data={"out": 12000.0, "hours": 8044.0})
    runtime.energy_in = None
    runtime.consumption_snapshot = cop.ConsumptionSnapshot()
    runtime.modbus = SimpleNamespace(answered=set(), descriptions=[])
    sensors = _sensors(sensor, runtime)
    for span in ("day", "year", "first_year", "lifetime"):
        assert "62341" in sensors[span].extra_state_attributes["skäl"], span
        assert sensors[span].extra_state_attributes["tillförd energi ur"] == "Modbus 62341"


def test_a_lifetime_below_the_floor_is_too_little_not_a_fault(sensor, cop):
    # A machine that has just started counting: 11 against 1.
    runtime = _runtime(cop, anchor=None, data={"out": 11.0, "in": 1.0, "hours": 30.0})
    lifetime = _sensors(sensor, runtime)["lifetime"]
    assert lifetime.native_value is None
    assert lifetime.extra_state_attributes["skäl"] == "för lite energi ännu, 1.0 av 10 kWh"
