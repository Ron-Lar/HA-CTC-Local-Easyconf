"""Decoding one register, with CTC's markers for a missing sensor.

CTC answers for hardware that is not fitted with its own markers, and two of
them are negative. They arrive as large unsigned words, so a check on the raw
word never catches them: 55537 is -9999, which used to come out as -999.9
degrees. A 32 bit counter that is not there reads all ones, which as a lifetime
energy figure would poison the long term statistics for good.
"""

from __future__ import annotations

import logging

import pytest

import ha_stub
from conftest import load


def _description(const, key: str):
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        if description.key == key:
            return description
    raise KeyError(key)


def _reading(modbus_api, const, key: str, *words: int):
    description = _description(const, key)
    raw = {description.address + offset: word for offset, word in enumerate(words)}
    return modbus_api.decode_reading(description, raw)


# ------------------------------------------------------ one signed register


def test_a_temperature_is_scaled_and_signed(modbus_api, const):
    assert _reading(modbus_api, const, "outdoor_temp", 215).value == pytest.approx(21.5)
    assert _reading(modbus_api, const, "outdoor_temp", 65436).value == pytest.approx(-10.0)


@pytest.mark.parametrize("word", [55537, 55536, 32768, 32767, 9999, 10000])
def test_every_marker_for_a_missing_sensor_is_judged_after_the_sign(modbus_api, const, word):
    """55537 is -9999 and 55536 is -10000; 32768 is -32768. None of them is a reading."""
    assert _reading(modbus_api, const, "outdoor_temp", word).value is None


def test_a_register_that_was_not_read_has_no_reading(modbus_api, const):
    description = _description(const, "outdoor_temp")
    assert modbus_api.decode_reading(description, {}).value is None


def test_pump_speeds_carry_one_decimal(modbus_api, const):
    # An EcoAir 720M at 66.2 per cent reports 662.
    assert _reading(modbus_api, const, "hp1_charge_pump", 662).value == pytest.approx(66.2)


# ------------------------------------------------- one unsigned counter


@pytest.mark.parametrize(
    "word, expected", [(32768, 32768), (32769, 32769), (40000, 40000), (65535, 65535), (32767, None)]
)
def test_a_one_word_counter_is_read_without_a_sign(modbus_api, const, word, expected):
    """62191 counts kWh in one word. Read with a sign, 32 769 came out as -32 767,
    which Home Assistant refuses for a total that only grows, so the statistics
    stood still for the rest of the word. 32767 stays the one word marker: one
    reading's gap, which a total tolerates."""
    assert _reading(modbus_api, const, "immersion_kwh", word).value == expected


def test_a_counter_in_one_word_is_unsigned_and_what_can_go_below_zero_is_not(const):
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        if description.count == 1 and description.state_class == "total_increasing":
            assert not description.signed, description.key
    # Degree minutes run negative when the house is behind, and a temperature
    # does in winter: the sign stays, and with it the negative markers.
    assert _description(const, "degree_minutes").signed
    assert _description(const, "outdoor_temp").signed


# --------------------------------------------------------- 32 bit counters


@pytest.mark.parametrize("key", ["compressor_kwh", "compressor_hours"])
def test_a_counter_of_all_ones_is_a_missing_counter_not_four_billion(modbus_api, const, key):
    assert _reading(modbus_api, const, key, 0xFFFF, 0xFFFF).value is None


def test_a_counter_is_least_significant_word_first(modbus_api, const):
    assert _reading(modbus_api, const, "compressor_kwh", 0x2345, 0x0001).value == 0x12345


def test_a_counter_that_has_reached_a_round_number_is_still_a_reading(modbus_api, const):
    """The single word markers are not applied to a pair: 10 000 kWh happens."""
    assert _reading(modbus_api, const, "compressor_kwh", 9999, 0).value == 9999
    assert _reading(modbus_api, const, "compressor_kwh", 10000, 0).value == 10000
    assert _reading(modbus_api, const, "compressor_hours", 32767, 0).value == 32767


def test_a_counter_missing_its_second_word_has_no_reading(modbus_api, const):
    assert _reading(modbus_api, const, "compressor_kwh", 1234).value is None


# ------------------------------------------------------------------- enums


def test_a_known_code_reads_as_its_label_and_keeps_the_code(modbus_api, const):
    reading = _reading(modbus_api, const, "system_status", 5)
    assert reading.value == "Varmvatten"
    assert reading.code == 5
    assert reading.unknown is None


def test_an_unlabelled_code_reads_as_unknown_with_the_number_kept(modbus_api, const):
    reading = _reading(modbus_api, const, "system_status", 12)
    assert reading.value == const.STATUS_UNKNOWN
    assert reading.code == 12
    assert reading.unknown == 12


def test_a_marker_on_an_enum_register_is_no_reading_at_all(modbus_api, const):
    reading = _reading(modbus_api, const, "system_status", 32767)
    assert reading.value is None
    assert reading.code is None


# ------------------------------------------- the coordinator leans on it


@pytest.fixture()
def modbus_coordinator(modbus_api):
    ha_stub.skip_unless_stubbed()
    coordinator = load("coordinator")
    client = modbus_api.CtcModbusClient("192.0.2.55")
    return coordinator.CtcModbusCoordinator(hass=object(), client=client, interval=30)


def test_the_coordinator_decodes_through_the_same_function(modbus_coordinator, const):
    outdoor = _description(const, "outdoor_temp")
    assert modbus_coordinator._decode(outdoor, {62000: 55537}) is None
    assert modbus_coordinator._decode(outdoor, {62000: 215}) == pytest.approx(21.5)
    kwh = _description(const, "compressor_kwh")
    assert modbus_coordinator._decode(kwh, {62341: 0xFFFF, 62342: 0xFFFF}) is None


def test_the_coordinator_keeps_an_unknown_code_for_the_sensor_and_says_so_once(
    modbus_coordinator, const, caplog
):
    status = _description(const, "system_status")
    with caplog.at_level(logging.INFO):
        assert modbus_coordinator._decode(status, {62005: 12}) == const.STATUS_UNKNOWN
        assert modbus_coordinator._decode(status, {62005: 12}) == const.STATUS_UNKNOWN
    assert modbus_coordinator.unknown_codes == {"system_status": 12}
    assert sum("code 12" in record.message for record in caplog.records) == 1
    # A labelled code again clears the attribute.
    assert modbus_coordinator._decode(status, {62005: 5}) == "Varmvatten"
    assert "system_status" not in modbus_coordinator.unknown_codes
