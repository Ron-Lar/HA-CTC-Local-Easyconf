"""The Modbus route for consumed energy, and what a zero on it means.

The i360's display counts delivered heat but not the energy consumed, so that
side comes from Modbus register 62341. The register answering zero used to be
read as the register being absent: no snapshot, no tracker, no sensors, and in
the report ``consumption_modbus`` false, until somebody reloaded the entry
after the counter had moved. These tests hold the route to its new meaning: a
register that answered is a register that answered, whatever it said, and a
zero is carried through to where it can be told apart from a new machine.
"""

from __future__ import annotations

import asyncio
import pathlib
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.data = data


def _i360(cop, consumed, hours):
    """An i360 as the set-up leaves it: heat from the display, consumption from Modbus."""
    snapshot = cop.ConsumptionSnapshot()
    snapshot.update("read", {"compressor_kwh": consumed})
    data = {"out": 12000.0}
    if hours is not None:
        data["hours"] = hours
    return SimpleNamespace(
        cop=cop.CopTracker(FakeStore()),
        web=SimpleNamespace(data=data),
        energy_out=SimpleNamespace(key="out", label="Avgiven energi"),
        energy_in=None,
        consumption_snapshot=snapshot,
        operating_hours=[SimpleNamespace(key="hours")] if hours is not None else None,
    )


def _report(cop, stats_extra, runtime):
    """The flags exactly as __init__._stats_extra_for works them out."""
    heat, consumed = cop.current_totals(runtime)
    fault = cop.counter_fault(heat, consumed, cop.powered_on_hours(runtime))
    return stats_extra.build_extra(
        "EcoZenith i360",
        has_display=True,
        control_enabled=False,
        page_count=1,
        read_failures=0,
        heat_counter=runtime.energy_out is not None,
        consumption_counter=runtime.energy_in is not None,
        consumption_modbus=cop.modbus_consumption_answered({"compressor_kwh": consumed}),
        heat_total=heat is not None,
        consumption_total=consumed is not None,
        cop_floor=consumed is not None and not fault and consumed < cop.MIN_CONSUMPTION_KWH,
        cop_stuck=fault == "stuck",
        cop_implausible=fault == "implausible",
        heat_total_kwh=heat if fault else None,
        consumption_total_kwh=consumed if fault else None,
        **cop.cop_for_report(runtime),
    )


# ------------------------------------------------------------ the register


def test_a_register_that_answers_zero_gives_a_reading_of_zero(cop):
    assert cop.modbus_consumption({"compressor_kwh": 0}) == 0.0
    assert cop.modbus_consumption({"compressor_kwh": 0.0}) == 0.0
    assert cop.modbus_consumption({"compressor_kwh": 9166}) == 9166.0


def test_no_reading_without_a_register_or_from_a_sentinel(cop):
    assert cop.modbus_consumption({}) is None
    assert cop.modbus_consumption(None) is None
    assert cop.modbus_consumption({"compressor_kwh": 4294967295}) is None
    assert cop.modbus_consumption({"compressor_kwh": True}) is None
    assert cop.modbus_consumption({"compressor_kwh": "0"}) is None
    # A counter cannot stand below zero, so that is not a reading either.
    assert cop.modbus_consumption({"compressor_kwh": -1}) is None


def test_an_answered_register_is_told_from_a_missing_one(cop):
    # Answered is about the key: the block was read and the controller replied.
    assert cop.modbus_consumption_answered({"compressor_kwh": 0}) is True
    assert cop.modbus_consumption_answered({"compressor_kwh": 9166}) is True
    # Even a sentinel is an answer; it is just not a number.
    assert cop.modbus_consumption_answered({"compressor_kwh": 4294967295}) is True
    assert cop.modbus_consumption_answered({"outdoor_temp": 3.5}) is False
    assert cop.modbus_consumption_answered({}) is False
    assert cop.modbus_consumption_answered(None) is False


# ------------------------------------------------------------- the snapshot


def test_the_snapshot_keeps_a_zero_from_modbus(cop):
    snapshot = cop.ConsumptionSnapshot()
    snapshot.update("t1", {"compressor_kwh": 0})
    assert snapshot.value == 0.0
    # The pairing holds until the display is read again, as for any value.
    snapshot.update("t1", {"compressor_kwh": 3})
    assert snapshot.value == 0.0
    snapshot.update("t2", {"compressor_kwh": 4294967295})
    assert snapshot.value is None


def test_totals_carry_a_zero_consumption_through(cop):
    runtime = _i360(cop, 0, hours=None)
    assert cop.current_totals(runtime) == (12000.0, 0.0)


# ------------------------------------------- what the zero means, and to whom


def test_a_counter_at_zero_on_a_running_i360_is_reported_stuck(cop, stats_extra):
    # The machine has been switched on for years and 62341 still says nothing:
    # the controller never writes it. The report says so, and only then sends
    # the two totals, which is what makes the zero visible at all.
    runtime = _i360(cop, 0, hours=20000)
    payload = _report(cop, stats_extra, runtime)
    features = payload["features"]
    assert features["consumption_modbus"] is True
    assert features["consumption_total"] is True
    assert features["heat_total"] is True
    assert features["cop_stuck"] is True
    assert features["cop_floor"] is False
    assert payload["metrics"] == {"heat_total_kwh": 12000.0, "consumption_total_kwh": 0.0}
    assert "står på noll" in cop.cop_reason(None, "lifetime", 12000.0, 0.0, 20000)


def test_a_new_machine_at_zero_is_waiting_not_broken(cop, stats_extra):
    # Switched on this morning: the counter has had no time. The flag says it
    # sits under the floor, nothing else, and no number leaves the house.
    runtime = _i360(cop, 0, hours=3)
    payload = _report(cop, stats_extra, runtime)
    features = payload["features"]
    assert features["consumption_modbus"] is True
    assert features["consumption_total"] is True
    assert features["cop_floor"] is True
    assert features["cop_stuck"] is False
    assert "metrics" not in payload
    reason = cop.cop_reason(None, "lifetime", 12000.0, 0.0, 3)
    assert "för lite energi" in reason and "0.0 av 10 kWh" in reason


def test_a_register_that_never_answered_is_reported_as_missing(cop, stats_extra):
    # The other signature: no key at all, so the report must say "no Modbus
    # route" rather than "a register at zero", or the two cannot be told apart.
    payload = stats_extra.build_extra(
        "EcoZenith i360",
        has_display=True,
        control_enabled=False,
        page_count=1,
        read_failures=0,
        consumption_counter=False,
        consumption_modbus=cop.modbus_consumption_answered({}),
    )
    assert payload["features"]["consumption_modbus"] is False


def test_the_tracker_takes_a_zero_consumption_in_its_stride(cop):
    # Recorded like any sample, divided by nothing: the figure stays empty
    # rather than the tracker falling over.
    tracker = cop.CopTracker(FakeStore())
    asyncio.run(tracker.async_record(12000.0, 0.0))
    yearly = tracker.result(12000.0, 0.0)
    assert yearly.value is None
    assert yearly.basis == "lifetime"
    assert tracker.result_day(12000.0, 0.0).value is None
    assert cop.cop_for_report(SimpleNamespace(
        cop=tracker,
        web=SimpleNamespace(data={"out": 12000.0}),
        energy_out=SimpleNamespace(key="out"),
        energy_in=None,
        consumption_snapshot=_i360(cop, 0, None).consumption_snapshot,
    ))["cop_lifetime"] is None


# ------------------------------------------------------------- the set-up


def test_the_set_up_builds_the_route_on_an_answered_register():
    # __init__.py needs Home Assistant and cannot be imported here, so the call
    # sites are checked as source: the snapshot and the tracker hang on the
    # register having answered, and the report flag means the same thing.
    source = (ROOT / "__init__.py").read_text(encoding="utf-8")
    assert "and modbus_consumption_answered(modbus.data)" in source, (
        "uppsättningen avgör Modbus-vägen på om registret svarade"
    )
    assert "consumption_modbus=modbus_consumption_answered(" in source, (
        "rapportens consumption_modbus betyder att registret svarade"
    )
    assert "modbus_consumption(modbus.data) is not None" not in source, (
        "en nolla vid uppsättning får inte avgöra Modbus-vägen"
    )
