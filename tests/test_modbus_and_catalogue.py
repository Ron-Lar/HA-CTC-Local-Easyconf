"""Tests for register decoding, block planning and the value catalogue."""

from __future__ import annotations

import pytest


# ------------------------------------------------------------------- Modbus


def test_signed_decoding_handles_negative_temperatures(modbus_api):
    assert modbus_api.decode_signed(100) == 100
    assert modbus_api.decode_signed(65436) == -100
    assert modbus_api.decode_signed(32767) == 32767
    assert modbus_api.decode_signed(32768) == -32768


def test_pair_decoding_is_least_significant_word_first(modbus_api):
    # CTC sends 32 bit counters low word first.
    assert modbus_api.decode_pair(0x0001, 0x0002) == 0x00020001


def test_sentinels_are_recognised(modbus_api):
    for missing in (9999, -9999, 10000, -10000, 32767):
        assert modbus_api.is_sentinel(missing)
    assert not modbus_api.is_sentinel(215)


def test_block_planner_groups_neighbours(modbus_api, const):
    sensor = const.ModbusSensor
    plan = modbus_api.plan_blocks(
        (
            sensor("a", 62000, "a"),
            sensor("b", 62003, "b"),
            sensor("c", 62005, "c"),
            sensor("far", 62300, "far"),
        )
    )
    assert plan == [(62000, 6), (62300, 1)]


def test_block_planner_respects_the_hundred_register_limit(modbus_api, const):
    sensor = const.ModbusSensor
    sensors = tuple(
        sensor(f"s{offset}", 62000 + offset, "s") for offset in range(0, 240, 8)
    )
    plan = modbus_api.plan_blocks(sensors)
    assert plan, "expected at least one block"
    assert all(count <= modbus_api.MAX_BLOCK for _, count in plan)


def test_block_planner_covers_every_configured_register(modbus_api, const):
    descriptions = const.MODBUS_SENSORS + const.MODBUS_SETTINGS
    plan = modbus_api.plan_blocks(descriptions)
    covered = {
        address
        for start, count in plan
        for address in range(start, start + count)
    }
    for description in descriptions:
        for offset in range(description.count):
            assert description.address + offset in covered


def test_block_planner_is_empty_for_no_sensors(modbus_api):
    assert modbus_api.plan_blocks(()) == []


# ---------------------------------------------------------------- catalogue


def test_scale_follows_the_display_format(catalogue):
    assert catalogue._decimals("%.1f°C") == pytest.approx(0.1)
    assert catalogue._decimals("%.-1f%%") == pytest.approx(0.1)
    assert catalogue._decimals("%d") == pytest.approx(1.0)


def test_unit_ignores_the_conversion_itself(catalogue):
    # "%" opens every conversion, so a naive search returns a percent sign for
    # a temperature.
    assert catalogue._unit("%.1f°C") == "°C"
    assert catalogue._unit("%.-1f%%") == "%"
    assert catalogue._unit("%.-1frps") == "rps"


def test_unit_falls_back_to_the_row_name(catalogue):
    assert catalogue._unit("%.1f", "Avgiven värme (kW)") == "kW"
    assert catalogue._unit("%.1f", "Utetemperatur °C") == "°C"
    assert catalogue._unit("%d", "Timer avfrostning") is None


def test_label_cleaning_drops_a_trailing_unit(catalogue):
    assert catalogue._clean_label("Avgiven värme (kW)") == "Avgiven värme"
    assert catalogue._clean_label("Utetemperatur °C") == "Utetemperatur"
    assert catalogue._clean_label("Kompressor") == "Kompressor"


def test_separators_are_not_readings(catalogue):
    assert catalogue.has_conversion("%.1f")
    assert not catalogue.has_conversion(" / ")
    assert not catalogue.has_conversion(" , ")


def test_numeric_value_filters_missing_sensors(catalogue, const):
    value = const.SlowValue(
        key="k", label="l", page=1, screen=2, fmt="%.1f", var_indices=[1], scale=0.1
    )
    assert catalogue.numeric_value(value, [0, 215]) == pytest.approx(21.5)
    assert catalogue.numeric_value(value, [0, 9999]) is None
    assert catalogue.numeric_value(value, [0]) is None
    assert catalogue.numeric_value(value, [0, "text"]) is None


def test_rows_break_on_the_left_hand_column(catalogue, web_api):
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=5, y=100, width=190, height=28, visible=True, label="Laddpump"),
        widget(index=1, kind=3, x=195, y=100, width=60, height=28, visible=True, label="Till"),
        widget(index=2, kind=3, x=255, y=100, width=40, height=28, visible=True, value_fmt="%.-1f%%", value_vars=[3]),
        widget(index=3, kind=3, x=5, y=128, width=190, height=28, visible=True, label="Brinepump"),
        widget(index=4, kind=3, x=195, y=128, width=60, height=28, visible=True, value_fmt="%.-1f%%", value_vars=[4]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[2] == "Laddpump"
    assert pairing[4] == "Brinepump"


def test_flow_layout_widgets_stay_on_their_row(catalogue, web_api):
    # A widget parked far to the left is laid out after the previous one, so it
    # belongs to the same row rather than starting a new one.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=5, y=50, width=190, height=28, visible=True, label="VP in/ut"),
        widget(index=1, kind=3, x=195, y=50, width=50, height=28, visible=True, value_fmt="%.1f", value_vars=[1]),
        widget(index=2, kind=3, x=-20000, y=50, width=10, height=28, visible=True, value_fmt=" / ", value_vars=[]),
        widget(index=3, kind=3, x=-20000, y=50, width=40, height=28, visible=True, value_fmt="%.1f", value_vars=[2]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[1] == "VP in/ut 1"
    assert pairing[3] == "VP in/ut 2"


def test_storage_round_trip_keeps_the_catalogue(catalogue, const):
    page = const.SlowPage(page=22, title="Driftinfo Värmepump", screens=[1, 0, 118])
    page.values.append(
        const.SlowValue(
            key="p22_avgiven_varme",
            label="Avgiven värme",
            page=22,
            screen=118,
            fmt="%.1f",
            var_indices=[97],
            unit="kW",
            scale=0.1,
        )
    )
    restored = catalogue.pages_from_storage(catalogue.pages_to_storage([page]))
    assert len(restored) == 1
    assert restored[0].title == "Driftinfo Värmepump"
    assert restored[0].values[0].unit == "kW"
    assert restored[0].values[0].var_indices == [97]


def test_storage_discards_damaged_entries(catalogue):
    assert catalogue.pages_from_storage([{"nonsense": True}]) == []
    assert catalogue.pages_from_storage(None) == []


# ---------------------------------------------------------------- discovery


def test_model_names_come_from_the_settings_file(discovery):
    found = discovery.DiscoveredDisplay(host="192.168.1.55", settings_name="settings_ezi2xx.bin")
    assert "i255" in found.model
    assert found.host in found.label
    other = discovery.DiscoveredDisplay(host="192.168.1.155", settings_name="settings_ezi5xx.bin")
    assert "i550" in other.model


def test_unknown_settings_file_still_names_something(discovery):
    found = discovery.DiscoveredDisplay(host="1.2.3.4", settings_name="settings_future.bin")
    assert "future" in found.model


def test_positional_suffix_does_not_hide_the_unit(catalogue):
    # A row with two readings gets " 1" and " 2" appended, which used to push the
    # unit out of reach of the pattern that looks at the end of the name.
    assert catalogue._unit("%.1f", "Brine in/ut °C 1") == "°C"
    assert catalogue._clean_label("Brine in/ut °C 2") == "Brine in/ut 2"
    assert catalogue._base_label("Hetgas/Suggas °C 3") == "Hetgas/Suggas °C"


def test_readings_without_a_caption_are_left_unnamed(catalogue, web_api):
    # Schematic pages place readings on a diagram with no caption beside them.
    # Naming them after whatever string is nearest produces confident nonsense.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=1, x=365, y=55, width=39, height=31, visible=True, label="Suomi"),
        widget(index=1, kind=3, x=420, y=55, width=55, height=33, visible=True, value_fmt="%.1f", value_vars=[16]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert 1 not in pairing


def test_pacing_holds_the_gap_between_transactions(modbus_api):
    # CTC documents an update rate for the BMS interface and cannot pipeline, so
    # the client must space requests out rather than send them back to back.
    import asyncio
    import time

    client = modbus_api.CtcModbusClient("192.168.1.55")

    async def scenario() -> float:
        client._last_request = time.monotonic()
        started = time.monotonic()
        await client._pace()
        return time.monotonic() - started

    waited = asyncio.run(scenario())
    assert waited >= modbus_api.MESSAGE_WAIT * 0.8


def test_pacing_does_not_wait_when_the_gap_has_passed(modbus_api):
    import asyncio
    import time

    client = modbus_api.CtcModbusClient("192.168.1.55")

    async def scenario() -> float:
        client._last_request = time.monotonic() - 10
        started = time.monotonic()
        await client._pace()
        return time.monotonic() - started

    assert asyncio.run(scenario()) < modbus_api.MESSAGE_WAIT


def test_a_tab_strip_is_tried_before_the_rest(catalogue, web_api):
    # The tabs along the bottom are what lead to the other pages. Trying the
    # schematic first is how the history page got missed.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=1, x=219, y=45, width=116, height=74, visible=True),
        widget(index=1, kind=1, x=66, y=141, width=79, height=73, visible=True),
        widget(index=2, kind=0, x=0, y=230, width=83, height=38, visible=True),
        widget(index=3, kind=0, x=80, y=230, width=83, height=38, visible=True),
        widget(index=4, kind=0, x=160, y=230, width=83, height=38, visible=True),
        widget(index=5, kind=0, x=240, y=230, width=83, height=38, visible=True),
    ]
    order = catalogue._order_targets(
        [(w.x, w.y, w.width, w.height) for w in widgets]
    )
    assert order[0][1] > 200, "en flik ska provas först"
    assert order[-1][1] < 200, "schemabilden sist"


def test_a_rule_at_the_left_edge_does_not_become_the_column(catalogue, web_api):
    # A divider drawn at x=0 used to be taken for the row name column, after
    # which no row broke and every reading was named after the first label.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=4, x=0, y=228, width=480, height=1, visible=True),
        widget(index=1, kind=3, x=5, y=55, width=190, height=28, visible=True, label="Total drifttid"),
        widget(index=2, kind=3, x=195, y=55, width=50, height=28, visible=True, value_fmt="%d", value_vars=[1]),
        widget(index=3, kind=3, x=5, y=83, width=190, height=28, visible=True, label="Avgiven värme totalt"),
        widget(index=4, kind=3, x=195, y=83, width=50, height=28, visible=True, value_fmt="%d", value_vars=[2]),
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[2] == "Total drifttid"
    assert pairing[4] == "Avgiven värme totalt"


def test_a_caption_block_is_matched_off_in_order(catalogue, web_api):
    # Some pages draw every caption first and then every reading. One caption
    # must not end up naming six unrelated numbers.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=5, y=55, width=190, height=28, visible=True, label="Total drifttid"),
        widget(index=1, kind=3, x=5, y=-28, width=190, height=28, visible=True, label="Energi el total"),
        widget(index=2, kind=3, x=5, y=-28, width=190, height=28, visible=True, label="Avgiven värme totalt"),
        widget(index=3, kind=3, x=195, y=-28, width=50, height=28, visible=True, value_fmt="%d", value_vars=[1]),
        widget(index=4, kind=3, x=195, y=-28, width=50, height=28, visible=True, value_fmt="%d", value_vars=[2]),
        widget(index=5, kind=3, x=195, y=-28, width=50, height=28, visible=True, value_fmt="%d", value_vars=[3]),
        widget(index=6, kind=3, x=195, y=-28, width=50, height=28, visible=True, value_fmt="%d", value_vars=[4]),
        widget(index=7, kind=3, x=195, y=-28, width=50, height=28, visible=True, value_fmt="%d", value_vars=[5]),
    ]
    pairing = catalogue._pair_labels(widgets)
    named = sorted(set(pairing.values()))
    assert "Avgiven värme totalt" in named
    assert not any(name.startswith("Avgiven värme totalt ") for name in pairing.values())


def test_the_page_heading_is_not_part_of_the_caption_block(catalogue, web_api):
    # Counting the heading in shifted every name one step down the list.
    widget = web_api.Widget
    widgets = [
        widget(index=0, kind=3, x=60, y=4, width=290, height=38, visible=True, label="Historisk driftinfo"),
        widget(index=1, kind=3, x=5, y=-28, width=190, height=28, visible=True, label="Total drifttid"),
        widget(index=2, kind=3, x=5, y=-28, width=190, height=28, visible=True, label="Energi el total"),
        widget(index=3, kind=3, x=5, y=-28, width=190, height=28, visible=True, label="Avgiven värme totalt"),
    ] + [
        widget(index=4 + n, kind=3, x=195, y=-28, width=50, height=28, visible=True,
               value_fmt="%d", value_vars=[n + 1])
        for n in range(5)
    ]
    pairing = catalogue._pair_labels(widgets)
    assert pairing[4] == "Total drifttid"
    assert pairing[5] == "Energi el total"
    assert pairing[6] == "Avgiven värme totalt"
    assert "Historisk driftinfo" not in pairing.values()


# ------------------------------------------- state classes of display readings


def test_lifetime_energy_counters_only_grow(catalogue):
    # The history page on an i550 Pro (page 30, screen 136) and on an i255.
    for label in ("Avgiven värme totalt", "Tillförd energi totalt", "Avgiven kyla totalt",
                  "Energi el total", "EMXXX", "Energy output total", "Avgiven energi"):
        assert catalogue.display_state_class("kWh", label) == "total_increasing", label


def test_a_period_of_energy_has_no_state_class(catalogue):
    # Home Assistant refuses "measurement" for energy, and a rolling window is
    # not monotonic, so "total_increasing" would be wrong as well.
    for label in ("Avgiven värme/30 dagar", "Tillförd energi/30 dagar", "Avgiven kyla/30 dagar",
                  "Energi el/30 dagar", "Energy output/30 days", "Avgiven energi/24h"):
        assert catalogue.display_state_class("kWh", label) is None, label


def test_other_readings_stay_measurements(catalogue):
    assert catalogue.display_state_class("kW", "Avgiven värme") == "measurement"
    assert catalogue.display_state_class("°C", "Hetgas") == "measurement"
    assert catalogue.display_state_class("%", "Laddpump") == "measurement"
    assert catalogue.display_state_class(None, "Status") is None


def test_a_reconnect_waits_out_the_last_close(modbus_api):
    """CTC hands its single client slot back a moment after the socket closes.

    A reload closes and connects again in the same breath, which the controller
    answers with a reset, so the new connection waits for the slot instead.
    """
    unit = ("192.0.2.5", 502)
    assert modbus_api.settle_wait(*unit, 1000.0) == 0.0  # never seen before
    modbus_api.note_close(*unit, 1000.0)
    assert modbus_api.settle_wait(*unit, 1000.0) == modbus_api.CLOSE_SETTLE
    assert modbus_api.settle_wait(*unit, 1004.0) == modbus_api.CLOSE_SETTLE - 4.0
    assert modbus_api.settle_wait(*unit, 1000.0 + modbus_api.CLOSE_SETTLE) == 0.0
    assert modbus_api.settle_wait(*unit, 2000.0) == 0.0
    # Another pump on the network is not kept waiting by this one.
    assert modbus_api.settle_wait("192.0.2.6", 502, 1000.0) == 0.0


def test_every_reading_is_created_switched_on(const):
    """What the unit offers is on from the start, and the page hides the rest.

    A reading this installation has only ever reported as zero is left off the
    page by seen.py, which is a better filter than guessing here.
    """
    off = [d.key for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS if not d.enabled_default]
    assert off == []


def test_control_is_allowed_from_the_start(const):
    """Both where an entry is created and where the runtime reads the option."""
    from conftest import COMPONENT

    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert "CONF_ENABLE_CONTROL: True," in flow
    assert "CONF_ENABLE_CONTROL: False," not in flow
    assert "options.get(CONF_ENABLE_CONTROL, True)" in flow
    setup = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "options.get(CONF_ENABLE_CONTROL, True)" in setup


# ------------------------------------------------- real pages, both displays

#: What each page's readings are called, by the variable the display holds them
#: in. Checked against the panel itself: the figures are the ones on the screen.
REAL_PAGES = {
    # Read against the panel: VP in 32,8 and ut 34,1 °C, the valve at 32,7 %,
    # the inverter's bus at 616 V.
    "i255_118": {
        41: "Laddpump", 47: "VP in/ut °C 1", 48: "VP in/ut °C 2", 49: "Utetemperatur °C",
        55: "Expansionsventil", 101: "Inverter DC-Bus spänning V", 108: "Kompressortemp °C",
        113: "Vätskerör temp °C", 116: "Avgiven värme (kW)", 122: "Tillförd effekt (kW)",
    },
    # The counters the coefficient of performance rests on: 23 171 kWh of heat
    # out against 9 357 kWh in, and 8 653 hours switched on.
    "i255_128": {
        20: "Total drifttid h", 21: "Drifttid total", 22: "Avgiven värme totalt (kWh)",
        23: "Antal starter /24 h", 24: "Högsta framledning °C", 25: "Energi el total (kWh)",
        32: "Antal starter", 47: "Energi el/30 dagar (kWh)",
        53: "Tillförd energi totalt (kWh)", 65: "Medeltemperatur ute/30 dagar °C",
    },
    "i550_136": {
        5: "Högsta framledning °C", 4: "Energi el total (kWh)",
        6: "Avgiven värme totalt (kWh)", 7: "Drift /24 h:m", 9: "EMXXX kWh",
        26: "Antal starter /24 h", 29: "Antal starter",
        # The pair that used to share one name: two captions in a row, two
        # readings after them, and the first caption owns the first reading.
        50: "Energi el/30 dagar (kWh)", 32: "Medeltemperatur ute/30 dagar °C",
        35: "Avgiven kyla totalt (kWh)", 38: "Tillförd energi totalt (kWh)",
        47: "Tillförd energi/30 dagar (kWh)",
    },
    "i550_138": {
        39: "Laddpump", 40: "Brine in/ut °C 1", 42: "VP in/ut °C 1", 89: "Flöde l/min",
        44: "Utetemperatur °C", 46: "Hetgas/Suggas °C 1", 110: "Kompressortemp °C",
        115: "Vätskerör temp °C", 118: "Avgiven värme (kW)", 121: "Avgiven kyla (kW)",
        124: "Tillförd effekt (kW)",
    },
}


def _named_by_variable(catalogue, web_api, data):
    widgets = [web_api.Widget(**w) for w in data["widgets"]]
    pairing = catalogue._pair_labels(widgets)
    return {
        w.value_vars[0]: pairing[w.index]
        for w in widgets if w.index in pairing and w.value_vars
    }


@pytest.mark.parametrize("name", sorted(REAL_PAGES))
def test_a_real_page_names_its_readings(catalogue, web_api, page, name):
    named = _named_by_variable(catalogue, web_api, page(name))
    for variable, expected in REAL_PAGES[name].items():
        assert named.get(variable) == expected, f"{name} variable {variable}"


def test_a_reading_the_panel_draws_without_a_caption_is_left_unnamed(catalogue, web_api, page):
    """The schematic figures on the i550's compressor page, and the row whose
    caption the display would not name."""
    assert 107 not in _named_by_variable(catalogue, web_api, page("i550_138"))
    assert 2 not in _named_by_variable(catalogue, web_api, page("i550_136"))


def test_a_heading_over_the_rows_names_nothing(catalogue, web_api, page):
    """"Kompressor" stands over the i255's history rows and owns no reading."""
    named = _named_by_variable(catalogue, web_api, page("i255_128"))
    assert "Kompressor" not in named.values()


# ----------------------------------------- a status code the table has no name for


def test_a_status_code_without_a_label_reads_as_an_option_and_keeps_its_number(const):
    assert const.enum_label(const.STATUS_SYSTEM, 5) == ("Varmvatten", None)
    # VSH's i255 answered 12. "Okänd (12)" is not one of the sensor's options, and
    # Home Assistant answers a state that is not with an exception on every write,
    # so the sensor stopped updating and the log filled up instead.
    assert const.enum_label(const.STATUS_SYSTEM, 12) == (const.STATUS_UNKNOWN, 12)
    assert const.STATUS_UNKNOWN not in const.STATUS_SYSTEM.values()


def test_the_reading_for_an_unlabelled_code_is_offered_as_an_option():
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "custom_components" / "ctc_ecozenith" / "sensor.py"
    ).read_text(encoding="utf-8")
    assert "[*description.enum.values(), STATUS_UNKNOWN]" in source
    assert '{"kod": code}' in source, "koden ska finnas kvar som attribut"
