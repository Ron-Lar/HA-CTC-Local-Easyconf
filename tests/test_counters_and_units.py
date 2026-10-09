"""Counters are counters: units, device classes and state classes of the rows
that count hours, starts and minutes, on the display and over Modbus (R14)."""

from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from conftest import COMPONENT, FIXTURES


def run(coro):
    return asyncio.run(coro)


class FakeClient:
    """Hands async_page_values the widgets of one real screen."""

    def __init__(self, data):
        self._screen = data["screen"]
        self._data = data

    async def async_widgets(self, screen):
        assert screen == self._screen
        from conftest import load

        widget = load("web_api").Widget
        return [widget(**w) for w in self._data["widgets"]]


def _page_values(catalogue, data, page=30):
    return run(catalogue.async_page_values(FakeClient(data), page, [data["screen"]]))


def _table(catalogue, values):
    """(key, unit, state class) per reading, which is what Home Assistant sees."""
    return [(v.key, v.unit, catalogue.display_state_class(v.unit, v.label)) for v in values]


# ------------------------------------------------------ the unit of a row name


def test_the_h_of_a_period_is_not_the_unit(catalogue):
    # "Antal starter /24 h" is starts per day. The h belongs to the 24.
    assert catalogue._unit("%d", "Antal starter /24 h") is None
    assert catalogue._unit("%d", "Number of starts /24 h") is None
    # Whereas a unit after a word, or after a number that is part of the name,
    # is still a unit.
    assert catalogue._unit("%d", "Total drifttid h") == "h"
    assert catalogue._unit("%.1f", "Framledning VS1 °C") == "°C"
    assert catalogue._unit("%d", "Energi el/30 dagar (kWh)") == "kWh"


def test_an_hour_counter_without_a_printed_unit_is_still_hours(catalogue):
    # The panel prints "Total drifttid h" and, on the next row, "Drifttid
    # total" with no h at all. Both are hours.
    assert catalogue._unit("%d", "Drifttid total") == "h"
    assert catalogue._unit("%d", "Total operation time") == "h"
    # A count of starts is not hours, and neither is anything over a period.
    assert catalogue._unit("%u", "Antal starter") is None
    assert catalogue._unit("%d", "Drifttid total/30 dagar") is None


def test_a_clock_row_is_read_in_minutes(catalogue):
    assert catalogue.is_clock_format("%02d:%02d")
    assert catalogue.is_clock_format("%d:%02d")
    assert not catalogue.is_clock_format("%02d")
    assert not catalogue.is_clock_format("%.1f")
    assert not catalogue.is_clock_format(None)
    assert catalogue._unit("%02d:%02d", "Drift /24 h:m") == "min"


def test_the_period_and_clock_suffixes_leave_the_name(catalogue):
    assert catalogue._clean_label("Antal starter /24 h") == "Antal starter /24"
    assert catalogue._clean_label("Drift /24 h:m") == "Drift /24"
    assert catalogue._clean_label("Total drifttid h") == "Total drifttid"
    assert catalogue._clean_label("Drifttid total") == "Drifttid total"
    # The positional suffix survives the cleaning as before.
    assert catalogue._clean_label("Drift /24 h:m 2") == "Drift /24 2"


# ----------------------------------------------------------- state classes


def test_lifetime_counters_only_grow_whatever_their_unit(catalogue):
    for unit, label in (
        ("h", "Total drifttid"),
        ("h", "Drifttid total"),
        ("h", "Total operation time"),
        (None, "Antal starter"),
        (None, "Number of starts"),
        (None, "Drifttid total"),
    ):
        assert catalogue.display_state_class(unit, label) == "total_increasing", (unit, label)
        assert catalogue.is_lifetime_counter(label), label


def test_the_same_names_over_a_period_are_not_counters(catalogue):
    assert catalogue.display_state_class(None, "Antal starter /24") is None
    assert catalogue.display_state_class("min", "Drift /24") == "measurement"
    assert catalogue.display_state_class("h", "Drifttid total/30 dagar") == "measurement"
    assert not catalogue.is_lifetime_counter("Antal starter /24")
    assert not catalogue.is_lifetime_counter("Drift /24")


def test_a_number_without_a_unit_still_has_no_state_class(catalogue):
    # A firmware version is a number too, and must never enter the statistics.
    assert catalogue.display_state_class(None, "Programversion VP-styrkort") is None
    assert catalogue.display_state_class(None, "Timer avfrostning") is None
    assert catalogue.display_state_class(None, "Kritiska larm räknare 1") is None
    assert catalogue.display_state_class(None, "Värde 8") is None


def test_the_energy_rules_are_unchanged(catalogue):
    assert catalogue.display_state_class("kWh", "Avgiven värme totalt") == "total_increasing"
    assert catalogue.display_state_class("kWh", "Avgiven värme/30 dagar") is None
    assert catalogue.display_state_class("kWh", "Avgiven energi/24h") is None
    assert catalogue.display_state_class("kW", "Avgiven värme") == "measurement"


# ------------------------------------------------------------ clock values


def _clock_value(const, fmt="%02d:%02d", indices=(27, 26)):
    return const.SlowValue(
        key="p30_drift_24_h_m", label="Drift /24", page=30, screen=128,
        fmt=fmt, var_indices=list(indices), unit="min",
    )


def test_hours_and_minutes_become_one_figure_in_minutes(catalogue, const):
    # The i255's panel read 03:46 the day the page was captured.
    raw = [0] * 26 + [46, 3]
    assert catalogue.numeric_value(_clock_value(const), raw) == 226.0
    assert catalogue.numeric_value(_clock_value(const, indices=(7, 8)), [0] * 9) == 0.0


def test_a_clock_row_with_a_missing_half_has_no_value(catalogue, const):
    assert catalogue.numeric_value(_clock_value(const), [0] * 26 + [46]) is None
    assert catalogue.numeric_value(_clock_value(const), [0] * 26 + [9999, 3]) is None
    assert catalogue.numeric_value(_clock_value(const), [0] * 26 + [46, "3"]) is None
    assert catalogue.numeric_value(_clock_value(const), [0] * 26 + [46, True]) is None


def test_a_plain_reading_still_scales_as_before(catalogue, const):
    value = const.SlowValue(key="k", label="l", page=1, screen=2, fmt="%.1f", var_indices=[1], scale=0.1)
    assert catalogue.numeric_value(value, [0, 215]) == pytest.approx(21.5)
    assert catalogue.numeric_value(value, [0, 9999]) is None


def test_two_integers_of_a_clock_row_fold_into_one_reading(catalogue):
    readings = [
        ("Total drifttid h", "%d", [20], 3),
        ("Drift /24 h:m 1", "%02d", [27], 14),
        ("Drift /24 h:m 2", "%02d", [26], 16),
        ("Antal starter /24 h", "%d", [23], 18),
    ]
    assert catalogue._join_clock_rows(readings) == [
        ("Total drifttid h", "%d", [20], 3),
        ("Drift /24 h:m", "%02d:%02d", [27, 26], 14),
        ("Antal starter /24 h", "%d", [23], 18),
    ]


def test_only_a_clock_row_folds(catalogue):
    # Two readings of an ordinary row keep their places and their numbers.
    pair = [("VP in/ut °C 1", "%.1f", [47], 24), ("VP in/ut °C 2", "%.1f", [48], 26)]
    assert catalogue._join_clock_rows(pair) == pair
    # And a clock row drawn in one widget is already whole.
    whole = [("Drift /24 h:m", "%02d:%02d", [7, 8], 12)]
    assert catalogue._join_clock_rows(whole) == whole


def test_a_clock_value_survives_storage(catalogue, const):
    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values.append(_clock_value(const))
    restored = catalogue.pages_from_storage(catalogue.pages_to_storage([page]))
    assert restored[0].values[0].var_indices == [27, 26]
    assert restored[0].values[0].fmt == "%02d:%02d"
    assert restored[0].values[0].unit == "min"


# --------------------------------------------- the four real pages, end to end

#: What the i255's history page becomes: the two energy counters and the
#: operating hours are sums, the day's compressor minutes a measurement, and
#: the periods carry no state class.
I255_HISTORY = [
    ("p30_total_drifttid", "h", "total_increasing"),
    ("p30_hogsta_framledning", "°C", "measurement"),
    ("p30_energi_el_total", "kWh", "total_increasing"),
    ("p30_emxxx", "kWh", "total_increasing"),
    ("p30_avgiven_varme_totalt", "kWh", "total_increasing"),
    ("p30_drift_24_h_m", "min", "measurement"),
    ("p30_antal_starter_24", None, None),
    ("p30_drifttid_total", "h", "total_increasing"),
    ("p30_antal_starter", None, "total_increasing"),
    ("p30_kritiska_larm_raknare_1", None, None),
    ("p30_kritiska_larm_raknare_2", None, None),
    ("p30_kritiska_larm_raknare_3", None, None),
    ("p30_kritiska_larm_raknare_4", None, None),
    ("p30_energi_el_30_dagar", "kWh", None),
    ("p30_avgiven_kyla_totalt", "kWh", "total_increasing"),
    ("p30_tillford_energi_totalt", "kWh", "total_increasing"),
    ("p30_avgiven_varme_30_dagar", "kWh", None),
    ("p30_avgiven_kyla_30_dagar", "kWh", None),
    ("p30_tillford_energi_30_dagar", "kWh", None),
    ("p30_medeltemperatur_ute_30_dagar", "°C", "measurement"),
]

#: The i550 Pro's history page. "Värde 8" is the row whose caption the display
#: would not give up, left as it is on purpose: its hours are zero there anyway.
I550_HISTORY = [
    ("p30_varde_8", None, None),
    ("p30_hogsta_framledning", "°C", "measurement"),
    ("p30_energi_el_total", "kWh", "total_increasing"),
    ("p30_avgiven_varme_totalt", "kWh", "total_increasing"),
    ("p30_drift_24_h_m", "min", "measurement"),
    ("p30_varde_13", None, None),
    ("p30_emxxx", "kWh", "total_increasing"),
    ("p30_antal_starter_24", None, None),
    ("p30_antal_starter", None, "total_increasing"),
    ("p30_energi_el_30_dagar", "kWh", None),
    ("p30_medeltemperatur_ute_30_dagar", "°C", "measurement"),
    ("p30_avgiven_kyla_totalt", "kWh", "total_increasing"),
    ("p30_tillford_energi_totalt", "kWh", "total_increasing"),
    ("p30_avgiven_varme_30_dagar", "kWh", None),
    ("p30_avgiven_kyla_30_dagar", "kWh", None),
    ("p30_tillford_energi_30_dagar", "kWh", None),
]


def test_the_i255_history_page_end_to_end(catalogue, page):
    values = _page_values(catalogue, page("i255_128"))
    assert _table(catalogue, values) == I255_HISTORY
    clock = next(v for v in values if v.key == "p30_drift_24_h_m")
    assert clock.label == "Drift /24"
    assert clock.fmt == "%02d:%02d" and clock.var_indices == [27, 26]
    # The two integers' keys, _1 and _2, are gone with the fold; the one row
    # carries the key the i550 has always had, so the name can improve freely.
    assert not any(v.key.startswith("p30_drift_24_h_m_") for v in values)
    assert "p30_drift_24" not in {v.key for v in values}


def test_the_i550_history_page_end_to_end(catalogue, page):
    values = _page_values(catalogue, page("i550_136"))
    assert _table(catalogue, values) == I550_HISTORY
    clock = next(v for v in values if v.key == "p30_drift_24_h_m")
    assert clock.label == "Drift /24"
    assert clock.var_indices == [7, 8]


@pytest.mark.parametrize("name, count", [("i255_118", 37), ("i550_138", 35)])
def test_the_operation_data_pages_are_untouched(catalogue, page, name, count):
    """No counters live on the heat pump pages, so nothing there changes:
    every state class follows the unit alone, and no row reads as time."""
    values = _page_values(catalogue, page(name), page=22)
    assert len(values) == count
    for value in values:
        assert value.unit not in ("h", "min"), value.key
        expected = None if not value.unit else "measurement"
        assert catalogue.display_state_class(value.unit, value.label) == expected, value.key
        assert len(value.var_indices) == 1, value.key


def test_the_powered_on_hours_are_still_the_only_anchor(catalogue, cop, page):
    # "Drifttid total" now carries h as well, and must not become a candidate
    # for the commissioning date: it is the compressor's time, always fewer.
    i255 = page("i255_128")
    found = cop.find_operating_hours([_fake_page(catalogue, i255)])
    assert [v.key for v in found] == ["p30_total_drifttid"]
    # The i550's row lost its caption and stays "Värde 8", by design.
    assert cop.find_operating_hours([_fake_page(catalogue, page("i550_136"))]) is None


def _fake_page(catalogue, data):
    class Page:
        values = _page_values(catalogue, data)

    return Page()


# ----------------------------------------------------------------- Modbus


def test_hours_and_minutes_are_durations(const):
    assert const.device_class_for("h") == "duration"
    assert const.device_class_for("min") == "duration"
    assert const.device_class_for("°C") == "temperature"
    assert const.device_class_for(None) is None
    assert const.device_class_for("%") is None
    # What the table declares wins over what the unit implies.
    assert const.device_class_for("kWh", "energy") == "energy"


def test_every_time_register_is_a_duration(const):
    timed = [d for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS if d.unit in ("h", "min")]
    assert {d.address for d in timed} >= {62214, 62234, 61503}
    for description in timed:
        assert const.device_class_for(description.unit, description.device_class) == "duration", description.key
    # The lifetime counter is a sum, the day's minutes and the hours left are not.
    by_key = {d.key: d for d in timed}
    assert by_key["compressor_hours"].state_class == "total_increasing"
    assert by_key["compressor_hours_24h"].state_class == "measurement"


def test_the_control_units_software_is_diagnostic(const):
    diagnostic = {d.address for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS if d.diagnostic}
    assert diagnostic == {62244, 62245}
    for description in const.MODBUS_SENSORS:
        if description.diagnostic:
            assert description.state_class is None, description.key
            assert description.unit is None, description.key


def test_the_sensor_platform_applies_both():
    source = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert '"duration": SensorDeviceClass.DURATION' in source
    assert "device_class_for(description.unit, description.device_class)" in source
    assert "device_class_for(unit)" in source, "displayraderna går samma väg"
    assert "if description.diagnostic:" in source
    assert "EntityCategory.DIAGNOSTIC" in source
    assert "UNIT_TO_CLASS" not in source, "en tabell, i const.py"
