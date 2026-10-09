"""The words the cards put on a state themselves, worked in node (V1).

The chips and the list wrote every state with hass.formatEntityState, which
follows the user's language in Home Assistant. An enum sensor's state is the
integration's own Swedish string, so that read right, but a binary sensor's
came out of Home Assistant's translations: on an English Home Assistant the
status line said "Värmepump status: Redo för start" beside "Kompressor i
drift: Not running", "Avfrostning: Off" and "Larm: OK".

The page now sends a small table of words under `states`, in the language the
page is built in (dashboard_views.state_words), and the cards word a binary
state out of it, by device class first and then plainly. Everything else is
left to Home Assistant as before, an older page without the table included.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

#: What dashboard_views.state_words gives a Swedish page, and an English one.
SV = {
    "on": "Till", "off": "Av", "problem_on": "Larm", "problem_off": "OK",
    "unset": "ej satt", "unset_note": "Pumpen lämnar inte ut sitt eget värde här.",
    "no_limit": "ingen gräns",
}
EN = {
    "on": "On", "off": "Off", "problem_on": "Alarm", "problem_off": "OK",
    "unset": "not set", "unset_note": "The pump does not give out its own value here.",
    "no_limit": "no limit",
}


def _run(program: str):
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _pure(script: str):
    """Run `script` with the card's pure helpers bound to `c`, no DOM about.

    The card hands its helpers over only when there is no customElements, so
    the stub DOM must not be loaded first here.
    """
    return _run(f"const c = require({json.dumps(str(CARD))});\n{script}\n")


def _node(script: str):
    """Run `script` with the page mount as `page` and the card's path as CARD."""
    return _run(
        f"const page = require({json.dumps(str(PAGE))});\n"
        f"const CARD = {json.dumps(str(CARD))};\n"
        f"{script}\n"
    )


def _binary(state: str, device_class: str | None = None) -> dict:
    attributes = {"device_class": device_class} if device_class else {}
    return {"state": state, "attributes": attributes}


#: The status line as the i255 has it: four binaries of three kinds and an enum.
STATES = {
    "binary_sensor.compressor": _binary("on", "running"),
    "binary_sensor.immersion": _binary("off", "running"),
    "binary_sensor.alarm": _binary("off", "problem"),
    "binary_sensor.defrosting": _binary("off"),
    "sensor.hp_status": {"state": "Redo för start", "attributes": {"device_class": "enum"}},
}


def _shown(card_name: str, field: str, states_table, hass_words=True) -> list:
    """What a mounted card shows for every entity in STATES, in order.

    The hass has a formatEntityState of its own that marks what it wrote, so
    the test can tell the page's word from Home Assistant's.
    """
    items = [{"entity": entity, "name": entity} for entity in STATES]
    config = {field: items}
    if states_table is not None:
        config["states"] = states_table
    hass = (
        "{formatEntityState: (s) => 'HA:' + s.state}" if hass_words else "{}"
    )
    return _node(f"""
      const card = page.mountCard(CARD, {json.dumps(card_name)}, {json.dumps(config)},
                                  {json.dumps(STATES)}, {hass});
      const values = page.all(card, (e) => e.className === "value");
      console.log(JSON.stringify(values.map((e) => e.textContent)));
    """)


# ------------------------------------------------------------ the pure helper


def test_a_binary_state_gets_the_pages_word_by_device_class_first_then_plainly():
    assert _pure(f"""
      const t = {json.dumps(SV)};
      console.log(JSON.stringify([
        c.ownWord("binary_sensor.a", {json.dumps(_binary("on", "running"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("off", "running"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("on", "problem"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("off", "problem"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("on"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("off"))}, t),
      ]));
    """) == ["Till", "Av", "Larm", "OK", "Till", "Av"]


def test_what_the_table_has_no_word_for_is_left_to_home_assistant():
    # An enum, a number, a binary with no value, a table without the word, no
    # table at all: undefined every time, which JSON writes as null.
    assert _pure(f"""
      const t = {json.dumps(SV)};
      console.log(JSON.stringify([
        c.ownWord("sensor.a", {{state: "Redo för start", attributes: {{}}}}, t),
        c.ownWord("number.a", {{state: "0", attributes: {{}}}}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("unavailable", "running"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("unknown"))}, t),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("on", "running"))}, {{}}),
        c.ownWord("binary_sensor.a", {json.dumps(_binary("on", "running"))}, undefined),
        c.ownWord("binary_sensor.a", undefined, t),
      ].map((v) => (v === undefined ? null : v))));
    """) == [None] * 7


def _number(state: str, **attributes) -> dict:
    return {"state": state, "attributes": {"unit_of_measurement": "rps", **attributes}}


def test_a_control_without_a_value_says_so_and_a_reading_without_one_is_left_alone():
    # V2: the hot water setpoint has no mirror register and is unknown until
    # written. A number or select in unknown is a control not set; a sensor in
    # unknown is Home Assistant's to word (and R35's to explain).
    assert _pure(f"""
      const t = {json.dumps(SV)};
      console.log(JSON.stringify([
        c.ownWord("number.a", {json.dumps(_number("unknown"))}, t),
        c.ownWord("select.a", {{state: "unknown", attributes: {{}}}}, t),
        c.ownWord("sensor.a", {{state: "unknown", attributes: {{}}}}, t),
        c.ownWord("number.a", {json.dumps(_number("unavailable"))}, t),
        c.ownWord("number.a", {json.dumps(_number("unknown"))}, {{on: "Till"}}),
      ].map((v) => (v === undefined ? null : v))));
    """) == ["ej satt", "ej satt", None, None, None]


def test_a_zero_the_pump_means_as_no_limit_is_a_word_for_the_pumps_own_value_only():
    # The compressor's top speed: 0 in the stored setting means no limit, and
    # the control mirrors it while nothing is written. A 0 written through the
    # control ("styrning aktiv": "ja") is shown as the 0 it is, and a key the
    # page did not mark keeps its number.
    assert _pure(f"""
      const t = {json.dumps(SV)};
      const mirrored = {json.dumps(_number("0.0", **{"styrning aktiv": "nej"}))};
      const written = {json.dumps(_number("0.0", **{"styrning aktiv": "ja"}))};
      console.log(JSON.stringify([
        c.ownWord("number.a", mirrored, t, "no_limit"),
        c.ownWord("number.a", written, t, "no_limit"),
        c.ownWord("sensor.a", {json.dumps(_number("0"))}, t, "no_limit"),
        c.ownWord("sensor.a", {json.dumps(_number("50.0"))}, t, "no_limit"),
        c.ownWord("sensor.a", {json.dumps(_number("0"))}, t, undefined),
        c.ownWord("sensor.a", {json.dumps(_number("0"))}, {{on: "Till"}}, "no_limit"),
      ].map((v) => (v === undefined ? null : v))));
    """) == ["ingen gräns", None, "ingen gräns", None, None, None]


# ---------------------------------------------------------------- the cards


def test_the_list_says_not_set_and_no_limit_in_the_pages_words():
    # Alla värden carries the controls and the stored settings as rows.
    states = {
        "number.dhw": _number("unknown"),
        "sensor.max_rps": _number("0.0"),
        "sensor.rps": _number("50.0"),
    }
    config = {
        "rows": [
            {"entity": "number.dhw", "name": "Varmvattenbörvärde"},
            {"entity": "sensor.max_rps", "name": "Inställt max varvtal", "zero_means": "no_limit"},
            {"entity": "sensor.rps", "name": "Varvtal", "zero_means": "no_limit"},
        ],
        "states": SV,
    }
    assert _node(f"""
      const card = page.mountCard(CARD, "ctc-ecozenith-rows", {json.dumps(config)},
                                  {json.dumps(states)});
      const values = page.all(card, (e) => e.className === "value");
      console.log(JSON.stringify(values.map((e) => e.textContent)));
    """) == ["ej satt", "ingen gräns", "50.0 rps"]


@pytest.mark.parametrize(
    "card_name, field",
    [("ctc-ecozenith-chips", "items"), ("ctc-ecozenith-rows", "rows")],
)
def test_the_status_line_reads_in_one_language(card_name, field):
    # The binaries in the page's words, the enum as Home Assistant has it.
    assert _shown(card_name, field, SV) == ["Till", "Av", "OK", "Av", "HA:Redo för start"]
    assert _shown(card_name, field, EN) == ["On", "Off", "OK", "Off", "HA:Redo för start"]


def test_an_alarm_is_a_word_of_its_own():
    states = dict(STATES, **{"binary_sensor.alarm": _binary("on", "problem")})
    assert _node(f"""
      const card = page.mountCard(CARD, "ctc-ecozenith-chips",
        {{items: [{{entity: "binary_sensor.alarm", name: "Larm"}}], states: {json.dumps(SV)}}},
        {json.dumps(states)});
      console.log(JSON.stringify(page.all(card, (e) => e.className === "value")[0].textContent));
    """) == "Larm"


def test_a_page_without_the_table_reads_as_it_did():
    # A browser that holds last week's page configuration with this week's
    # card script: Home Assistant's wording throughout, nothing blank.
    assert _shown("ctc-ecozenith-chips", "items", None) == [
        "HA:on", "HA:off", "HA:off", "HA:off", "HA:Redo för start",
    ]
    # And with neither a table nor formatEntityState, the raw state.
    assert _shown("ctc-ecozenith-chips", "items", None, hass_words=False) == [
        "on", "off", "off", "off", "Redo för start",
    ]
