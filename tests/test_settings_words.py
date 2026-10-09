"""The pump's own settings read as words (V3), checked without Home Assistant.

"Värmepump tillåten" (61521) showed as 1 on the performance tab and in the
full list, because the register had no enum, while "Inställt värmeläge"
beside it read as a word. The stored settings are the 61500 block, read only
and never written; the one boolean among them now has Ja and Nej. The rest
are a mode with an enum of its own, a quantity with a unit, or a plain number
(the heating curve's slope and adjustment), none of which is a 1 that means
yes, so nothing else under "Pumpens inställningar" is left as a raw number.

An enum sensor's state has to be among its options (0.15.2's trap: Home
Assistant refuses a state outside them and the sensor stops updating), so a
code outside the table reads STATUS_UNKNOWN with the code beside it, as for
every other enum register.
"""

from __future__ import annotations


def _description(const, key: str):
    for description in const.MODBUS_SENSORS + const.MODBUS_SETTINGS:
        if description.key == key:
            return description
    raise KeyError(key)


def test_whether_the_heat_pump_is_allowed_reads_yes_or_no(modbus_api, const):
    allowed = _description(const, "set_hp1_blocked")
    assert allowed.address == 61521
    assert allowed.enum is const.YES_NO
    assert modbus_api.decode_reading(allowed, {61521: 1}) == modbus_api.Reading("Ja", code=1)
    assert modbus_api.decode_reading(allowed, {61521: 0}) == modbus_api.Reading("Nej", code=0)
    # A code the table has no word for is an option like any other, with the
    # code kept beside it (0.15.2).
    assert modbus_api.decode_reading(allowed, {61521: 2}) == modbus_api.Reading(
        const.STATUS_UNKNOWN, code=2, unknown=2
    )
    # Not read at all, or marked missing: no reading, not a "Nej".
    assert modbus_api.decode_reading(allowed, {}) == modbus_api.Reading(None)
    assert modbus_api.decode_reading(allowed, {61521: 32767}) == modbus_api.Reading(None)


def test_the_words_are_ones_a_sensor_can_say(const):
    # sensor.py offers [*enum.values(), STATUS_UNKNOWN] as the options, so the
    # labels must not repeat and must differ from the word for an unknown code.
    labels = list(const.YES_NO.values())
    assert labels == ["Nej", "Ja"]
    assert len(set(labels)) == len(labels)
    assert const.STATUS_UNKNOWN not in labels


def test_no_stored_setting_is_left_as_a_bare_number_that_means_yes_or_no(const):
    # Every setting without a unit is a mode with words, or one of the two
    # heating curve numbers, which are quantities of their own.
    bare = [d.key for d in const.MODBUS_SETTINGS if d.unit is None and d.enum is None]
    assert bare == ["set_slope_1", "set_adjust_1"]
    # And the modes all have words, with no code's label repeating.
    for description in const.MODBUS_SETTINGS:
        if description.enum is not None:
            labels = list(description.enum.values())
            assert len(set(labels)) == len(labels), description.key


def test_the_explanation_says_what_ctc_calls_the_register_and_what_the_words_mean(explanations):
    text = explanations.MODBUS["set_hp1_blocked"]
    assert "Värmepump tillåten" in text and "61521" in text
    assert "Ja" in text and "Nej" in text
    assert "spärrad" in text


def test_the_setting_keeps_its_place_under_the_pumps_settings(dashboard_views):
    (settings,) = [keys for sid, _icon, keys in dashboard_views._TECHNICAL if sid == "settings"]
    assert "set_hp1_blocked" in settings
    assert "set_heating_mode_1" in settings
