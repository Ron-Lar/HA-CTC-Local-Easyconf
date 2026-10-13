"""What Home Assistant holds and what the pump says beside it, without Home Assistant (R49).

The controls carry control_active and the pump's own value where a stored
setting mirrors them, the hot water mode shows the pump's own program while
idle, the sensor Aktiva överstyrningar counts and lists what is held, and the
overview shows it with the button beside it while anything is held. Under a
real core the same runs in test_homeassistant_overrides.py.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load

WRITTEN = datetime(2026, 10, 13, 9, 30, tzinfo=timezone.utc)
#: When the value in force was set, before the keepalive's last write (F6.2).
SET = datetime(2026, 10, 13, 9, 12, tzinfo=timezone.utc)
DEVICE = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module", autouse=True)
def _platforms_load():
    # Names the package's config entry type, which the platforms import, and
    # puts the stand-ins in place where Home Assistant is not installed.
    ha_stub.install()


@pytest.fixture(scope="module")
def overrides():
    return load("overrides")


class Control:
    """The control manager as the entities and the sensor read it."""

    def __init__(self, active: dict[int, int] | None = None) -> None:
        self.active = dict(active or {})
        self.sets: list[tuple[int, int | None]] = []

    def get(self, address):
        return self.active.get(address)

    def written_at(self, address):
        return WRITTEN if address in self.active else None

    def set_at(self, address):
        return SET if address in self.active else None

    def written_attributes(self, address):
        if address not in self.active:
            return {}
        return {
            "senast skriven": WRITTEN.isoformat(timespec="seconds"),
            "gäller till": (WRITTEN + timedelta(minutes=5)).isoformat(timespec="seconds"),
        }

    def async_add_listener(self, listener):
        return lambda: None

    async def async_set(self, address, raw):
        self.sets.append((address, raw))
        if raw is None:
            self.active.pop(address, None)
        else:
            self.active[address] = raw


def _modbus(const, data=None, codes=None, success=True):
    return SimpleNamespace(
        descriptions=const.MODBUS_SENSORS + const.MODBUS_SETTINGS,
        data=dict(data or {}),
        codes=dict(codes or {}),
        last_update_success=success,
    )


def _runtime(const, active=None, **modbus):
    return SimpleNamespace(
        control=Control(active),
        modbus=_modbus(const, **modbus),
        device=DEVICE,
        control_enabled=True,
    )


def _register(const, key):
    return const.CONTROL_BY_KEY[key]


# ------------------------------------------------------------- what is held


def test_nothing_held_is_an_empty_list(overrides):
    assert overrides.held(Control()) == []


def test_each_held_register_with_its_name_value_and_moment_in_table_order(overrides):
    held = overrides.held(Control({1007: 2, 1010: 215}))
    assert held == [
        {"namn": "Styr rumsbörvärde", "register": "1010", "värde": 21.5,
         "satt": SET.isoformat(timespec="seconds")},
        {"namn": "Styr varmvattenläge", "register": "1007", "värde": "Komfort",
         "satt": SET.isoformat(timespec="seconds")},
    ]


def test_the_list_carries_when_the_value_was_set_not_when_it_was_last_written(overrides):
    """F6.2: the keepalive moves the last write every minute, and the list must not follow it."""
    (held,) = overrides.held(Control({1010: 215}))
    assert held["satt"] == SET.isoformat(timespec="seconds")
    assert "senast skriven" not in held


def test_a_mode_without_a_name_shows_its_code(overrides, const):
    assert overrides.shown_value(_register(const, "ctl_dhw_mode"), 9) == 9
    assert overrides.shown_value(_register(const, "ctl_extra_dhw"), 3) == 1.5


# ---------------------------------------------------------- the pump's value


def test_the_pumps_value_comes_from_the_mirror_and_only_from_a_round_that_answered(overrides, const):
    room = _register(const, "ctl_room_setpoint_1")
    assert overrides.pump_number(_modbus(const, {"set_room_1": 20.5}), room) == 20.5
    assert overrides.pump_number(_modbus(const, {"set_room_1": 20.5}, success=False), room) is None
    assert overrides.pump_number(_modbus(const, {}), room) is None
    # No mirror, no value: the hot water setpoint has none, and 62001 is not it.
    setpoint = _register(const, "ctl_dhw_setpoint")
    assert setpoint.mirror is None
    assert overrides.pump_number(_modbus(const, {"dhw_stop_temp": 55.0}), setpoint) is None


def test_the_pumps_mode_is_judged_on_the_code(overrides, const):
    mode = _register(const, "ctl_dhw_mode")
    shown = _modbus(const, {"set_dhw_mode": "Komfort"}, {"set_dhw_mode": 2})
    assert overrides.pump_option(shown, mode) == "Komfort"
    # A reworded label is no other mode: the code decides.
    reworded = _modbus(const, {"set_dhw_mode": "Comfort"}, {"set_dhw_mode": 2})
    assert overrides.pump_option(reworded, mode) == "Komfort"
    unknown = _modbus(const, {"set_dhw_mode": "Okänd"}, {"set_dhw_mode": 7})
    assert overrides.pump_option(unknown, mode) is None
    assert overrides.pump_option(_modbus(const, {}, {"set_dhw_mode": 2}), mode) is None
    lost = _modbus(const, {"set_dhw_mode": "Komfort"}, {"set_dhw_mode": 2}, success=False)
    assert overrides.pump_option(lost, mode) is None
    # The zone mode has no mirror: 61542 has other modes and does not mirror 1015.
    zone = _register(const, "ctl_zone_mode_1")
    assert zone.mirror is None
    assert overrides.pump_option(_modbus(const, {"set_heating_mode_1": "Auto"}, {"set_heating_mode_1": 0}), zone) is None


# --------------------------------------------------------------- the numbers


def test_a_number_carries_the_pumps_value_also_while_it_holds_its_own(const):
    number = load("number")
    room = _register(const, "ctl_room_setpoint_1")
    runtime = _runtime(const, {1010: 225}, data={"set_room_1": 20.5})
    entity = number.CtcControlNumber(runtime, room)
    assert entity.native_value == 22.5
    shown = entity.extra_state_attributes
    assert shown["pumpens värde"] == shown["pump_value"] == 20.5
    assert shown["control_active"] is True
    runtime.control.active.clear()
    assert entity.native_value == 20.5
    assert entity.extra_state_attributes["pump_value"] == 20.5


def test_a_number_without_a_mirror_has_no_pumps_value(const):
    number = load("number")
    runtime = _runtime(const, data={"dhw_stop_temp": 55.0})
    shown = number.CtcControlNumber(runtime, _register(const, "ctl_dhw_setpoint")).extra_state_attributes
    assert "pumpens värde" not in shown and "pump_value" not in shown


def test_the_extra_hot_water_carries_61503_as_it_reads(const):
    number = load("number")
    extra = _register(const, "ctl_extra_dhw")
    assert extra.mirror == 61503
    runtime = _runtime(const, {1006: 2}, data={"set_extra_dhw": 0.0})
    shown = number.CtcControlNumber(runtime, extra).extra_state_attributes
    # Whatever 61503 reads while 1006 is held, it is what the pump says; R50 settles the rest.
    assert shown["pump_value"] == 0.0 and shown["control_active"] is True


# --------------------------------------------------------------- the selects


def test_the_hot_water_mode_shows_the_pumps_program_while_idle(const):
    select = load("select")
    mode = _register(const, "ctl_dhw_mode")
    runtime = _runtime(const, data={"set_dhw_mode": "Normal"}, codes={"set_dhw_mode": 1})
    entity = select.CtcControlSelect(runtime, mode)
    assert entity.current_option == "Normal"
    assert select.RELEASE in entity._attr_options, "släppet finns kvar i listan"
    idle = entity.extra_state_attributes
    assert idle["styrning aktiv"] == "nej" and idle["control_active"] is False
    assert idle["pumpens värde"] == idle["pump_value"] == "Normal"
    assert "last_written" not in idle

    runtime.control.active[1007] = 2
    assert entity.current_option == "Komfort"
    held = entity.extra_state_attributes
    assert held["control_active"] is True and held["styrning aktiv"] == "ja"
    assert held["pump_value"] == "Normal", "pumpens eget program står bredvid"
    assert held["last_written"] == held["senast skriven"]
    assert held["valid_until"] == held["gäller till"]


def test_a_mode_the_pump_already_has_is_written_when_chosen(const):
    """An automation or an energy manager that picks the mode the list shows still holds it."""
    select = load("select")
    runtime = _runtime(const, data={"set_dhw_mode": "Normal"}, codes={"set_dhw_mode": 1})
    entity = select.CtcControlSelect(runtime, _register(const, "ctl_dhw_mode"))
    assert entity.current_option == "Normal"
    run(entity.async_select_option("Normal"))
    assert runtime.control.sets == [(1007, 1)]
    run(entity.async_select_option(select.RELEASE))
    assert runtime.control.sets == [(1007, 1), (1007, None)]
    assert entity.current_option == "Normal"


def test_a_lost_round_keeps_the_select_available_and_idle_on_the_release(const):
    select = load("select")
    runtime = _runtime(const, data={"set_dhw_mode": "Normal"}, codes={"set_dhw_mode": 1}, success=False)
    entity = select.CtcControlSelect(runtime, _register(const, "ctl_dhw_mode"))
    assert entity.available is True
    assert entity.current_option == select.RELEASE
    assert entity.extra_state_attributes["pump_value"] is None


def test_the_zone_mode_and_the_price_mode_stay_on_the_release_while_idle(const):
    select = load("select")
    for key in ("ctl_zone_mode_1", "ctl_price_mode"):
        runtime = _runtime(const, data={"set_heating_mode_1": "Auto"}, codes={"set_heating_mode_1": 0})
        entity = select.CtcControlSelect(runtime, _register(const, key))
        assert entity.current_option == select.RELEASE, key
        shown = entity.extra_state_attributes
        assert "pump_value" not in shown and shown["control_active"] is False, key


# ------------------------------------------------------------------ the sensor


def test_the_sensor_counts_and_lists_what_is_held_in_both_languages(const):
    sensor = load("sensor")
    runtime = _runtime(const, {1010: 215, 1007: 2})
    entity = sensor.CtcActiveOverridesSensor(runtime)
    assert entity._attr_unique_id == "ctc_ecozenith_192.0.2.55_active_overrides"
    assert entity.native_value == 2
    assert entity.available is True
    shown = entity.extra_state_attributes
    assert [item["namn"] for item in shown["styrningar"]] == ["Styr rumsbörvärde", "Styr varmvattenläge"]
    assert shown["overrides"][0] == {
        "name": "Styr rumsbörvärde", "register": "1010", "value": 21.5,
        "set_at": SET.isoformat(timespec="seconds"),
    }
    runtime.control.active.clear()
    assert entity.native_value == 0
    assert entity.extra_state_attributes == {"styrningar": [], "overrides": []}


def test_the_sensor_comes_with_control_and_not_without(const, cop):
    ha_stub.skip_unless_stubbed()
    sensor = load("sensor")
    from test_cop_sensor import _runtime as cop_runtime

    def built(control_enabled):
        runtime = cop_runtime(cop)
        runtime.control = Control()
        runtime.control_enabled = control_enabled
        added = []
        entry = SimpleNamespace(runtime_data=runtime, entry_id="test", options={}, async_on_unload=lambda f: None)
        run(sensor.async_setup_entry(None, entry, added.extend))
        return [entity for entity in added if isinstance(entity, sensor.CtcActiveOverridesSensor)]

    assert len(built(True)) == 1
    assert built(False) == []


def test_the_sensor_is_explained_and_says_where_it_comes_from(explanations):
    text = explanations.explain("active_overrides")
    assert "styrningar (overrides)" in text and "fem minuter" in text
    assert explanations.source("active_overrides")


def test_the_controls_explain_the_pumps_value_and_where_there_is_none(explanations):
    for key in ("ctl_room_setpoint_1", "ctl_dhw_mode", "ctl_max_rps", "ctl_extra_dhw"):
        assert "pumpens värde (pump_value)" in explanations.explain(key), key
    assert "62001" in explanations.explain("ctl_dhw_setpoint")
    assert "61542" in explanations.explain("ctl_zone_mode_1")
    assert "61500" in explanations.explain("ctl_dhw_mode")


# ------------------------------------------------------------------- the page


def _pump(pumps):
    pump = pumps["vsh"]
    pump["entities"]["active_overrides"] = "sensor.ctc_ecozenith_i255_aktiva_overstyrningar"
    pump["names"]["active_overrides"] = "Aktiva överstyrningar"
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    pump["names"]["release_control"] = "Släpp all styrning"
    return pump


def test_the_overview_shows_what_is_held_with_the_button_beside_it(dashboard_views, pumps):
    pump = _pump(pumps)
    sections = dashboard_views.overview_sections(pump, dashboard_views.TEXT["sv"], (2026, 9))
    headings = [section["cards"][0]["heading"] for section in sections]
    assert headings[:3] == ["CTC EcoZenith i255", "Styrs från Home Assistant", "Snabbstyrning"]
    held = sections[1]
    sensor = pump["entities"]["active_overrides"]
    condition = {"condition": "numeric_state", "entity": sensor, "above": 0}
    chips, release = held["cards"][1:]
    assert chips["type"] == "custom:ctc-ecozenith-chips"
    (chip,) = chips["items"]
    assert chip["entity"] == sensor and chip["color"] == "info" and "on_state" not in chip
    assert chip["explanation"].startswith("Hur många")
    assert release["type"] == "custom:ctc-ecozenith-controls"
    (button,) = release["items"]
    assert button["entity"] == pump["entities"]["release_control"]
    # Only while anything is held: both cards, and so the section as a whole.
    assert chips["visibility"] == [condition] and release["visibility"] == [condition]
    assert held["visibility"] == [{"condition": "or", "conditions": [condition, condition]}]
    # The status line above it is untouched and always there.
    assert "visibility" not in sections[0]


def test_without_the_sensor_there_is_no_section(dashboard_views, pumps):
    pump = pumps["vsh"]
    pump["entities"]["release_control"] = "button.ctc_ecozenith_i255_slapp_all_styrning"
    sections = dashboard_views.overview_sections(pump, dashboard_views.TEXT["en"], (2026, 9))
    assert "Held by Home Assistant" not in [section["cards"][0]["heading"] for section in sections]


def test_the_sensor_heads_the_control_group_in_all_values(dashboard_views, pumps):
    pump = _pump(pumps)
    (section,) = dashboard_views.values_sections(pump, dashboard_views.TEXT["sv"], (2026, 9))
    rows = section["cards"][1]["rows"]
    start = rows.index({"heading": "Styrning"})
    assert rows[start + 1]["entity"] == pump["entities"]["active_overrides"]
    assert rows[start + 1]["name"] == "Aktiva överstyrningar"
