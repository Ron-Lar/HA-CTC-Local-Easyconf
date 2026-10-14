"""Extra hot water shows what Home Assistant holds and nothing of 61503 (roadmap R50).

The one test, on an i255 on 2026-10-13: Home Assistant held 1006 at one hour
for seven minutes, the keepalive wrote it every minute and every write reached
the unit, and 61503, Extra varmvatten kvar, stood at 0.0 the whole time. So
61503 is no mirror of the control. The number no longer carries it as the
pump's value, stands empty at rest rather than claiming to show the time left,
and the explanations of the control and of the stored register say what the
test showed. Writing and the keepalive are unchanged. Under a real core the
same runs in test_homeassistant_extra_hot_water.py.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from conftest import load
import ha_stub

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"
DEVICE = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}


@pytest.fixture(scope="module", autouse=True)
def _platforms_load():
    ha_stub.install()


class Control:
    """The control manager as the number and the sensor read it."""

    def __init__(self, active=None):
        self.active = dict(active or {})

    def get(self, address):
        return self.active.get(address)

    def set_at(self, address):
        return None

    def written_attributes(self, address):
        return {}

    def async_add_listener(self, listener):
        return lambda: None


def _runtime(const, active=None, data=None):
    return SimpleNamespace(
        control=Control(active),
        modbus=SimpleNamespace(
            descriptions=const.MODBUS_SENSORS + const.MODBUS_SETTINGS,
            data=dict(data or {}),
            codes={},
            last_update_success=True,
        ),
        device=DEVICE,
        control_enabled=True,
    )


# ------------------------------------------------------------- the register


def test_the_control_has_no_mirror_and_is_written_as_before(const):
    extra = const.CONTROL_BY_KEY["ctl_extra_dhw"]
    assert extra.address == 1006 and extra.mirror is None
    # Still one of the addresses the write path lets through, and 61503 never.
    assert 1006 in const.CONTROL_ADDRESSES
    assert 61503 not in const.CONTROL_ADDRESSES
    # The stored register stays a sensor of its own.
    stored = next(d for d in const.MODBUS_SETTINGS if d.key == "set_extra_dhw")
    assert stored.address == 61503


def test_the_other_mirrors_are_untouched(const):
    mirrors = {
        r.key: r.mirror for r in const.CONTROL_NUMBERS + const.CONTROL_SELECTS if r.mirror
    }
    assert mirrors == {
        "ctl_max_rps": 61572,
        "ctl_immersion_lower": 61590,
        "ctl_immersion_upper": 61591,
        "ctl_room_setpoint_1": 61509,
        "ctl_dhw_mode": 61500,
    }


# ---------------------------------------------------------------- the number


@pytest.mark.parametrize("left", [0.0, 1.5])
def test_at_rest_the_number_is_empty_whatever_61503_reads(const, left):
    number = load("number")
    entity = number.CtcControlNumber(
        _runtime(const, data={"set_extra_dhw": left}), const.CONTROL_BY_KEY["ctl_extra_dhw"]
    )
    assert entity.native_value is None, "inget påstående om tiden som är kvar"
    shown = entity.extra_state_attributes
    assert "pump_value" not in shown and "pumpens värde" not in shown
    assert shown["control_active"] is False and shown["register"] == "1006"


def test_held_the_number_shows_what_home_assistant_holds_and_nothing_beside_it(const):
    number = load("number")
    entity = number.CtcControlNumber(
        _runtime(const, {1006: 2}, data={"set_extra_dhw": 0.0}),
        const.CONTROL_BY_KEY["ctl_extra_dhw"],
    )
    assert entity.native_value == 1.0
    shown = entity.extra_state_attributes
    assert shown["control_active"] is True
    assert "pump_value" not in shown and "pumpens värde" not in shown


def test_the_overrides_sensor_lists_the_hold_and_says_nothing_of_61503(const):
    overrides = load("overrides")
    (item,) = overrides.held(Control({1006: 2}))
    assert item == {"namn": "Styr extra varmvatten", "register": "1006", "värde": 1.0, "satt": None}
    sensor = load("sensor")
    entity = sensor.CtcActiveOverridesSensor(_runtime(const, {1006: 2}, {"set_extra_dhw": 0.0}))
    shown = entity.extra_state_attributes
    assert shown["overrides"] == [
        {"name": "Styr extra varmvatten", "register": "1006", "value": 1.0, "set_at": None}
    ]
    assert "61503" not in json.dumps(shown, ensure_ascii=False)


# ----------------------------------------------------------- the explanations


def test_the_control_says_what_the_test_showed_and_claims_no_time_left(explanations):
    text = explanations.explain("ctl_extra_dhw")
    assert "Utan styrning visas tiden som är kvar" not in text
    assert "pumpens värde" not in text and "pump_value" not in text
    assert "står tomt utan styrning" in text
    for words in ("61503", "i255", "sju minuter", "en timme", "inte känt"):
        assert words in text, words
    assert "startar om sin timer vid varje omskrivning" in text
    # The keepalive is still explained, since it still runs.
    assert "varje minut" in text and "fem minuter" in text


def test_the_control_promises_no_start_for_so_many_hours_and_says_to_release(explanations):
    # The opening sentence no longer states as fact that the control starts
    # Extra hot water for the hours set: it gives that as CTC's register list
    # has it, says the keepalive may make it last longer, and names the button
    # that ends it, Släpp in Swedish and Release in English (F4.2, R50).
    sv = explanations.explain("ctl_extra_dhw", "sv")
    en = explanations.explain("ctl_extra_dhw", "en")
    assert sv != en
    assert "Startar Extra varmvatten i så här många timmar" not in sv
    assert "Starts Extra hot water for this many hours" not in en
    assert sv.startswith("Skriver Extra varmvatten (1006)")
    assert en.startswith("Writes Extra hot water (1006)")
    for words in ("registerlista", "Släpp", "pågå längre"):
        assert words in sv, words
    for words in ("register list", "Release", "last longer"):
        assert words in en, words
    assert "Släpp" not in en


def test_the_stored_register_says_what_the_test_showed(explanations):
    text = explanations.explain("set_extra_dhw")
    for words in ("i255", "sju minuter", "Styr extra varmvatten", "inte känt", "inte provat"):
        assert words in text, words
    assert "startar om sin timer vid varje omskrivning" in text


def test_neither_explanation_uses_a_dash_between_clauses(explanations):
    for key in ("ctl_extra_dhw", "set_extra_dhw"):
        text = explanations.explain(key)
        assert "–" not in text and "—" not in text and " - " not in text, key


def test_the_readme_no_longer_says_the_control_carries_61503(const):
    readme = (TESTS.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Control")[1].split("\n## ")[0]
    assert "for extra hot water the time left" not in section
    assert "Extra hot water carries none" in section
    assert "not known" in section


# ------------------------------------------------------------------ the card


def _node(program: str) -> str:
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


ENTITY = "number.ctc_ecozenith_i255_styr_extra_varmvatten"
RANGE = {"min": 0, "max": 12, "step": 0.5, "unit_of_measurement": "h"}
WORDS = {
    "controlled": "Styrd",
    "pump_value": "pumpen själv",
    "unset": "ej satt",
    "unset_note": "Pumpen lämnar inte ut sitt eget värde här.",
    "unread": "ej läst",
    "unread_note": "Pumpens eget värde gick inte att läsa.",
}


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_card_calls_it_not_set_at_rest_rather_than_not_read():
    idle = {"state": "unknown", "attributes": {**RANGE, "control_active": False}}
    out = _node(
        f"const c = require({json.dumps(str(CARD))});\n"
        f"console.log(JSON.stringify(c.ownWord({json.dumps(ENTITY)}, {json.dumps(idle)}, "
        f"{json.dumps(WORDS)})));\n"
    )
    assert json.loads(out) == "ej satt"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_the_card_shows_no_line_for_the_pumps_value_held_or_at_rest():
    states = [
        {ENTITY: {"state": "unknown", "attributes": {**RANGE, "control_active": False}}},
        {ENTITY: {"state": "1.0", "attributes": {**RANGE, "control_active": True}}},
    ]
    config = {
        "items": [{"entity": ENTITY, "name": "Styr extra varmvatten", "explanation": "x"}],
        "states": WORDS,
    }
    program = f"""
      const page = require({json.dumps(str(PAGE))});
      const card = page.mountCard(
        {json.dumps(str(CARD))}, "ctc-ecozenith-controls", {json.dumps(config)});
      const read = () => page.all(card, (e) => e.className === "control").map((row) => {{
        const find = (cls) => page.all({{shadowRoot: row}}, (e) => e.className === cls)[0];
        const mark = find("held");
        const pump = find("pump");
        return {{held: mark ? !mark.hidden : null,
                 pump: pump && !pump.hidden ? pump.textContent : null}};
      }});
      const seen = [];
      for (const states of {json.dumps(states)}) {{
        card.hass = {{ states, callService: () => Promise.resolve() }};
        seen.push(read());
      }}
      console.log(JSON.stringify(seen));
    """
    idle, held = json.loads(_node(program))
    assert idle == [{"held": False, "pump": None}]
    assert held == [{"held": True, "pump": None}]
