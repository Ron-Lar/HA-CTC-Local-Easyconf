"""The cards in Home Assistant's card picker, worked in node (roadmap L11).

The page's four cards were only custom elements: nothing put them in the card
picker, and a card picked by its type had no configuration to start from. Now
all five, the section card first, are listed in window.customCards with a name
and a description in the language Home Assistant is shown in, and each card
class answers getStubConfig with a configuration the card takes: the section
card names the first of the integration's heat pumps, a page card lists a few
of the integration's own entities. The section card's editor is drawn by Home
Assistant from getConfigForm, and the sections it offers are the ones
section_card.py knows, under the same names.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from conftest import load

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
CARD = ROOT / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SECTION = "ctc-ecozenith-section"
PAGE_CARDS = {
    "chips": "ctc-ecozenith-chips",
    "readings": "ctc-ecozenith-readings",
    "controls": "ctc-ecozenith-controls",
    "rows": "ctc-ecozenith-rows",
}
DEVICE = "0123456789abcdef0123456789abcdef"


def _state(state: str, **attributes) -> dict:
    return {"state": state, "attributes": attributes}


#: A Home Assistant with one CTC heat pump and a lamp of somebody else's: the
#: frontend's registry entries (hass.entities) and the states.
HASS = {
    "states": {
        "sensor.ctc_status": _state("Redo för start", device_class="enum", friendly_name="Status"),
        "sensor.ctc_hp_status": _state("Av", device_class="enum", friendly_name="VP status"),
        "sensor.ctc_outdoor": _state("7.2", unit_of_measurement="°C", friendly_name="Ute"),
        "sensor.ctc_flow": _state("35.2", unit_of_measurement="°C", friendly_name="Framledning"),
        "sensor.ctc_serial": _state("720825400001", friendly_name="Serienummer"),
        "sensor.ctc_hidden": _state("1", unit_of_measurement="h", friendly_name="Dold"),
        "number.ctc_room": _state("21.0", min=10, max=30, step=0.1, friendly_name="Rum"),
        "select.ctc_dhw": _state("Ekonomi", options=["Ekonomi", "Normal"], friendly_name="VV"),
        "light.lamp": _state("on", friendly_name="Lampa"),
    },
    "entities": {
        "sensor.ctc_status": {"entity_id": "sensor.ctc_status", "platform": "ctc_ecozenith",
                              "device_id": DEVICE},
        "sensor.ctc_hp_status": {"entity_id": "sensor.ctc_hp_status",
                                 "platform": "ctc_ecozenith", "device_id": DEVICE},
        "sensor.ctc_outdoor": {"entity_id": "sensor.ctc_outdoor", "platform": "ctc_ecozenith",
                               "device_id": DEVICE},
        "sensor.ctc_flow": {"entity_id": "sensor.ctc_flow", "platform": "ctc_ecozenith",
                            "device_id": DEVICE},
        "sensor.ctc_serial": {"entity_id": "sensor.ctc_serial", "platform": "ctc_ecozenith",
                              "device_id": DEVICE, "entity_category": "diagnostic"},
        "sensor.ctc_hidden": {"entity_id": "sensor.ctc_hidden", "platform": "ctc_ecozenith",
                              "device_id": DEVICE, "hidden": True},
        # Every number and select of the integration is a configuration
        # entity (number.py, select.py), and the frontend says so.
        "number.ctc_room": {"entity_id": "number.ctc_room", "platform": "ctc_ecozenith",
                            "device_id": DEVICE, "entity_category": "config"},
        "select.ctc_dhw": {"entity_id": "select.ctc_dhw", "platform": "ctc_ecozenith",
                           "device_id": DEVICE, "entity_category": "config"},
        "light.lamp": {"entity_id": "light.lamp", "platform": "hue", "device_id": "lamp"},
    },
}
EMPTY = {"states": {"light.lamp": HASS["states"]["light.lamp"]},
         "entities": {"light.lamp": HASS["entities"]["light.lamp"]}}


def _run(program: str):
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _browser(script: str, lang: str = "sv"):
    """Run `script` after the card has loaded on the stub DOM, Home Assistant in `lang`."""
    return _run(
        f"const page = require({json.dumps(str(PAGE))});\n"
        f"document.documentElement = {{ lang: {json.dumps(lang)} }};\n"
        f"require({json.dumps(str(CARD))});\n"
        f"{script}\n"
    )


# ------------------------------------------------------------- the picker


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_all_five_cards_are_in_the_picker_the_section_card_first(lang):
    listed = _browser("console.log(JSON.stringify(globalThis.customCards));", lang)
    assert [card["type"] for card in listed] == [SECTION, *PAGE_CARDS.values()]
    for card in listed:
        assert card["name"].startswith("CTC: "), card
        assert card["description"], card
        assert card["preview"] is True
        assert card["documentationURL"].endswith("#the-section-card")
    names = [card["name"] for card in listed]
    if lang == "sv":
        assert names[0] == "CTC: sektion av sidan"
        assert all("sektionskortet" in card["description"] for card in listed[1:])
    else:
        assert names[0] == "CTC: section of the page"
        assert all("section card" in card["description"] for card in listed[1:])


def test_a_second_load_of_the_script_lists_no_card_twice():
    listed = _browser(
        f"delete require.cache[require.resolve({json.dumps(str(CARD))})];\n"
        f"require({json.dumps(str(CARD))});\n"
        "console.log(JSON.stringify(globalThis.customCards.map((card) => card.type)));"
    )
    assert listed == [SECTION, *PAGE_CARDS.values()]


def test_the_documentation_link_points_at_a_heading_in_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert re.search(r"^#+ The section card$", readme, re.MULTILINE)


#: A dash used as a separator: a spaced hyphen, or an en or em dash anywhere.
DASH = re.compile("\\s-\\s|[\N{EN DASH}\N{EM DASH}]")


def test_the_blocks_texts_use_no_dash_as_a_separator():
    # Every string the picker and the section card put on the screen, the
    # Swedish ones above all: what the picker lists, and every literal in the
    # block, the card's own words among them.
    listed = _browser("console.log(JSON.stringify(globalThis.customCards));", "sv")
    block = CARD.read_text(encoding="utf-8").split("the card picker and the section card (L11)")[1]
    literals = re.findall(r'"((?:[^"\\]|\\.)*)"', block)
    texts = [card[field] for card in listed for field in ("name", "description")] + literals
    assert any("Värmepumpen är inte igång" in text for text in texts)
    for text in texts:
        assert not DASH.search(text), text


# ------------------------------------------------------------- the stubs


def test_every_card_class_has_a_stub_its_card_takes():
    # Mounted with its own stub, as the picker's preview does: setConfig must
    # take it, and the card draws the entities it names.
    found = _browser(
        f"const hass = {json.dumps(HASS)};\n"
        "const out = {};\n"
        f"for (const tag of {json.dumps([SECTION, *PAGE_CARDS.values()])}) {{\n"
        "  const Card = customElements.get(tag);\n"
        "  const stub = Card.getStubConfig(hass, [], []);\n"
        "  const card = new Card();\n"
        "  card.setConfig(stub);\n"
        "  out[tag] = stub;\n"
        "}\n"
        "console.log(JSON.stringify(out));"
    )
    assert found[SECTION] == {"device_id": DEVICE, "section": "temperatures"}
    assert found["ctc-ecozenith-chips"] == {"items": [
        {"entity": "sensor.ctc_hp_status", "name": "VP status"},
        {"entity": "sensor.ctc_status", "name": "Status"},
    ]}
    assert found["ctc-ecozenith-readings"] == {"items": [
        {"entity": "sensor.ctc_flow", "name": "Framledning"},
        {"entity": "sensor.ctc_outdoor", "name": "Ute"},
    ]}
    assert found["ctc-ecozenith-controls"] == {"items": [
        {"entity": "number.ctc_room", "name": "Rum"},
        {"entity": "select.ctc_dhw", "name": "VV"},
    ]}
    rows = found["ctc-ecozenith-rows"]["rows"]
    assert [row["entity"] for row in rows] == [
        "sensor.ctc_flow", "sensor.ctc_hp_status", "sensor.ctc_outdoor", "sensor.ctc_status",
    ]


def test_a_stub_shows_only_the_integrations_own_visible_entities():
    stubs = _browser(
        f"const hass = {json.dumps(HASS)};\n"
        f"console.log(JSON.stringify({json.dumps(list(PAGE_CARDS.values()))}"
        ".map((tag) => customElements.get(tag).getStubConfig(hass))));"
    )
    named = {item["entity"] for stub in stubs for item in stub.get("items", stub.get("rows", []))}
    assert "light.lamp" not in named, "en annan integrations entitet"
    assert "sensor.ctc_hidden" not in named, "en dold entitet"
    assert "sensor.ctc_serial" not in named, "en diagnostisk entitet"


def test_the_controls_stub_lets_configuration_entities_in_and_nothing_else_does():
    # F5.1: the controls are configuration entities, so a stub that sorted
    # every entity_category away was always empty for the controls card. Its
    # stub lets "config" in; a diagnostic or hidden entity stays out there,
    # a button that is configuration too is no control, and the other stubs
    # keep configuration entities out.
    hass = json.loads(json.dumps(HASS))
    extra = {
        "button.ctc_release_all": (_state("unknown", friendly_name="Släpp all styrning"),
                                   {"entity_category": "config"}),
        "number.ctc_diag": (_state("3", min=0, max=10, step=1, friendly_name="Diag"),
                            {"entity_category": "diagnostic"}),
        "select.ctc_hidden": (_state("Av", options=["Av"], friendly_name="Dold lista"),
                              {"entity_category": "config", "hidden": True}),
        "sensor.ctc_config": (_state("4", unit_of_measurement="h", friendly_name="Konfig"),
                              {"entity_category": "config"}),
    }
    for entity_id, (state, entry) in extra.items():
        hass["states"][entity_id] = state
        hass["entities"][entity_id] = {"entity_id": entity_id, "platform": "ctc_ecozenith",
                                       "device_id": DEVICE, **entry}
    stubs = _browser(
        f"const hass = {json.dumps(hass)};\n"
        f"console.log(JSON.stringify(Object.fromEntries({json.dumps(list(PAGE_CARDS.items()))}"
        ".map(([kind, tag]) => [kind, customElements.get(tag).getStubConfig(hass)]))));"
    )
    assert stubs["controls"] == {"items": [
        {"entity": "number.ctc_room", "name": "Rum"},
        {"entity": "select.ctc_dhw", "name": "VV"},
    ]}
    for kind in ("chips", "readings", "rows"):
        named = {item["entity"] for item in stubs[kind].get("items", stubs[kind].get("rows", []))}
        assert "sensor.ctc_config" not in named, kind


def test_without_a_heat_pump_the_page_cards_get_empty_lists_and_the_section_card_says_why():
    found = _browser(
        f"const hass = {json.dumps(EMPTY)};\n"
        "const out = {};\n"
        f"for (const tag of {json.dumps(list(PAGE_CARDS.values()))}) {{\n"
        "  const Card = customElements.get(tag);\n"
        "  const stub = Card.getStubConfig(hass);\n"
        "  new Card().setConfig(stub);\n"
        "  out[tag] = stub;\n"
        "}\n"
        f"const Section = customElements.get({json.dumps(SECTION)});\n"
        "out.section = Section.getStubConfig(hass);\n"
        "try { new Section().setConfig(out.section); out.error = null; }\n"
        "catch (err) { out.error = err.message; }\n"
        "console.log(JSON.stringify(out));"
    )
    assert found["ctc-ecozenith-chips"] == {"items": []}
    assert found["ctc-ecozenith-rows"] == {"rows": []}
    assert found["section"] == {"device_id": "", "section": "temperatures"}
    assert found["error"] == "ctc-ecozenith-section needs device_id"


def test_the_section_card_refuses_a_section_it_does_not_know():
    error = _browser(
        f"const Section = customElements.get({json.dumps(SECTION)});\n"
        f"const config = {{device_id: {json.dumps(DEVICE)}, section: 'graph_cop'}};\n"
        "try { new Section().setConfig(config); console.log(JSON.stringify(null)); }\n"
        "catch (err) { console.log(JSON.stringify(err.message)); }"
    )
    assert error.startswith('ctc-ecozenith-section has no section "graph_cop"')
    assert "temperatures" in error and "energy" in error


# ------------------------------------------------------------- the editor


@pytest.mark.parametrize("lang", ["sv", "en"])
def test_the_editor_picks_a_heat_pump_of_this_integration_and_a_section(lang):
    # The sections and their names are section_card.py's own, in its order.
    form = _browser(
        f"const Section = customElements.get({json.dumps(SECTION)});\n"
        "const form = Section.getConfigForm();\n"
        "console.log(JSON.stringify({schema: form.schema,"
        " labels: form.schema.map((field) => form.computeLabel(field))}));",
        lang,
    )
    device, section = form["schema"]
    assert device == {
        "name": "device_id", "required": True,
        "selector": {"device": {"filter": {"integration": "ctc_ecozenith"}}},
    }
    assert section["name"] == "section" and section["required"] is True
    options = section["selector"]["select"]["options"]
    section_card = load("section_card")
    assert [option["value"] for option in options] == list(section_card.SECTIONS)
    assert [option["label"] for option in options] == [
        section_card.label(s, lang) for s in section_card.SECTIONS
    ]
    words = {"sv": ["Värmepump", "Sektion"], "en": ["Heat pump", "Section"]}
    assert form["labels"] == words[lang]
