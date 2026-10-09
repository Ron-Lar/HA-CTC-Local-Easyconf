"""The derived binary sensors judge codes, and the immersion heater sees both.

Four of them used to compare against the Swedish labels in const.py, which the
coordinator produced after throwing the code away; a respelled or translated
label would have switched all four off without a word. And "Elpatron aktiv"
watched the lower heater only, while in the EcoZenith tanks it is the upper one,
in the hot water part, that does most of the work.
"""

from __future__ import annotations

import pytest

import ha_stub
from conftest import load


@pytest.fixture(scope="module")
def binary_sensor():
    ha_stub.install()
    return load("binary_sensor")


def _item(binary_sensor, key: str):
    return next(item for item in binary_sensor.DERIVED if item.key == key)


# --------------------------------------------------- each test, against codes


def test_the_compressor_runs_in_heating_cooling_and_hot_water(binary_sensor, const):
    test = _item(binary_sensor, "compressor_running").test
    for code in (3, 5, 33):
        assert test(code), code
    for code in set(const.STATUS_HEATPUMP) - {3, 5, 33}:
        assert not test(code), code


@pytest.mark.parametrize(
    "key, code",
    [("defrosting", 4), ("alarm", 7), ("blocked", 6)],
)
def test_one_status_code_each(binary_sensor, const, key, code):
    test = _item(binary_sensor, key).test
    assert test(code)
    for other in set(const.STATUS_HEATPUMP) - {code}:
        assert not test(other), other


def test_smartgrid_is_active_in_every_mode_but_normal(binary_sensor, const):
    test = _item(binary_sensor, "smartgrid_active").test
    assert not test(0)
    for code in set(const.SG_MODE) - {0}:
        assert test(code), code


def test_the_status_binaries_take_the_code_not_the_label(binary_sensor):
    for key in ("compressor_running", "defrosting", "alarm", "blocked", "smartgrid_active"):
        item = _item(binary_sensor, key)
        assert item.by_code, key
        assert item.sources[0] in ("hp1_status", "sg_mode")


def test_the_immersion_heater_is_active_when_either_heater_gives_power(binary_sensor):
    item = _item(binary_sensor, "immersion_active")
    assert item.sources == ("immersion_upper_kw", "immersion_lower_kw")
    assert not item.by_code
    assert item.test(0, 0) is False
    assert item.test(1.5, 0) is True          # the upper one, topping up hot water
    assert item.test(0, 3.0) is True
    assert item.test(None, 2.0) is True       # a unit without the upper register
    assert item.test(None, None) is False


def test_both_heaters_are_shown_as_attributes(binary_sensor):
    item = _item(binary_sensor, "immersion_active")
    assert dict(item.attributes) == {
        "elpatron övre": "immersion_upper_kw",
        "elpatron nedre": "immersion_lower_kw",
    }


# ------------------------------------------------- the codes behind the labels


def test_the_codes_are_pinned_to_their_labels(const):
    """Fails when a label in const.py changes spelling, so the codes, the
    explanations and any translation are looked over together."""
    assert {const.STATUS_HEATPUMP[code] for code in const.HP_RUNNING_CODES} == {
        "Till värme", "Till kyla", "Till varmvatten",
    }
    assert const.STATUS_HEATPUMP[const.HP_DEFROST_CODE] == "Avfrostning"
    assert const.STATUS_HEATPUMP[const.HP_ALARM_CODE] == "Av, larm"
    assert const.STATUS_HEATPUMP[const.HP_BLOCKED_CODE] == "Av, blockerad"
    assert const.SG_MODE[const.SG_NORMAL_CODE] == "Normal"


# -------------------------------------------------- deriving from the readings


def test_a_code_is_only_trusted_for_a_reading_that_is_there(binary_sensor):
    running = _item(binary_sensor, "compressor_running")
    assert binary_sensor.derive(running, {"hp1_status": "Till värme"}, {"hp1_status": 3}) is True
    # The label without the code, or a stale code without the label: unknown.
    assert binary_sensor.derive(running, {"hp1_status": "Till värme"}, {}) is None
    assert binary_sensor.derive(running, {}, {"hp1_status": 3}) is None


def test_the_immersion_heater_is_judged_from_whichever_heater_answers(binary_sensor):
    heater = _item(binary_sensor, "immersion_active")
    assert binary_sensor.derive(heater, {"immersion_upper_kw": 1.5}, {}) is True
    assert binary_sensor.derive(heater, {"immersion_lower_kw": 0.0}, {}) is False
    assert binary_sensor.derive(heater, {}, {}) is None


# ------------------------------------------ the coordinator keeps the codes


@pytest.fixture()
def modbus_coordinator(modbus_api):
    ha_stub.skip_unless_stubbed()
    coordinator = load("coordinator")
    client = modbus_api.CtcModbusClient("192.0.2.55")
    return coordinator.CtcModbusCoordinator(hass=object(), client=client, interval=30)


def _description(const, key: str):
    return next(d for d in const.MODBUS_SENSORS if d.key == key)


def test_the_coordinator_keeps_the_code_beside_the_label(modbus_coordinator, const):
    status = _description(const, "hp1_status")
    assert modbus_coordinator._decode(status, {62017: 33}) == "Till varmvatten"
    assert modbus_coordinator.codes == {"hp1_status": 33}
    # An unlabelled code is still a code.
    assert modbus_coordinator._decode(status, {62017: 99}) == const.STATUS_UNKNOWN
    assert modbus_coordinator.codes == {"hp1_status": 99}
    # A marker, or a register that was not read, takes the code away again.
    assert modbus_coordinator._decode(status, {62017: 32767}) is None
    assert modbus_coordinator.codes == {}
    modbus_coordinator._decode(status, {62017: 3})
    modbus_coordinator._decode(status, {})
    assert modbus_coordinator.codes == {}


def test_a_plain_number_leaves_no_code(modbus_coordinator, const):
    outdoor = _description(const, "outdoor_temp")
    modbus_coordinator._decode(outdoor, {62000: 215})
    assert modbus_coordinator.codes == {}


# ------------------------------------------------------------- the entity


class FakeCoordinator:
    def __init__(self, data, codes, ok=True):
        self.data = data
        self.codes = codes
        self.last_update_success = ok


class FakeRuntime:
    device = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}

    def __init__(self, data, codes, ok=True):
        self.modbus = FakeCoordinator(data, codes, ok)


def test_the_entity_reads_the_code_and_goes_unavailable_without_the_reading(binary_sensor):
    running = _item(binary_sensor, "compressor_running")
    entity = binary_sensor.CtcDerivedBinary(
        FakeRuntime({"hp1_status": "Till värme"}, {"hp1_status": 3}), running
    )
    assert entity.is_on is True
    assert entity.available is True
    assert entity.extra_state_attributes is None
    gone = binary_sensor.CtcDerivedBinary(FakeRuntime({}, {}), running)
    assert gone.is_on is None
    assert gone.available is False


def test_the_entity_shows_both_heaters_and_is_on_for_the_upper_one(binary_sensor):
    heater = _item(binary_sensor, "immersion_active")
    entity = binary_sensor.CtcDerivedBinary(
        FakeRuntime({"immersion_upper_kw": 3.0, "immersion_lower_kw": 0.0}, {}), heater
    )
    assert entity.is_on is True
    assert entity.available is True
    assert entity.extra_state_attributes == {"elpatron övre": 3.0, "elpatron nedre": 0.0}
    # One register is enough to be available; the other shows as absent.
    half = binary_sensor.CtcDerivedBinary(FakeRuntime({"immersion_lower_kw": 0.0}, {}), heater)
    assert half.available is True
    assert half.is_on is False
    assert half.extra_state_attributes == {"elpatron övre": None, "elpatron nedre": 0.0}


# ------------------------------------------------- the words around it


def test_the_explanation_and_the_source_name_both_heaters(explanations):
    text = explanations.DERIVED["immersion_active"]
    assert "övre" in text and "nedre" in text
    source = explanations.source("immersion_active")
    assert "62168" in source and "62169" in source
