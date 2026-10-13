"""Names and the CTC page follow Home Assistant's language, under a real core (R23).

The integration's own entities are named from their translations: Swedish where
Home Assistant is set to Swedish, the names they have always had, and English
otherwise, the Dutch installation's included. Here that is held where only a
real core can show it: a new installation gets entity ids from the names of
its language, as Home Assistant builds them, so a Swedish one gets the ids it
always got and an English one ids in English; an installation from before the
translations, upgraded under an English Home Assistant, keeps every entity id
and gets the English name, and a name its owner gave an entity stays. The
states that automations, EMS and the history rest on stay the controller's
Swedish words in every language, the binaries that read the status codes go on
working, and the page is built in Home Assistant's language whatever language
the panel is read in, the display's own rows under the panel's names.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_language.py
"""

from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.helpers import entity_registry as er
from homeassistant.loader import async_get_integration
from pytest_homeassistant_custom_component.common import MockConfigEntry

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    IDENTITY,
    MODEL,
    FakeModbus,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs_fixture,
)

from custom_components.ctc_ecozenith import dashboard
from custom_components.ctc_ecozenith.const import (
    CONF_IDENTITY,
    CONF_MENU_VERSION,
    CONF_MODBUS_PORT,
    CONF_SLAVE,
    CONF_WEB_PORT,
    DOMAIN,
)

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
SV = json.loads((COMPONENT / "translations" / "sv.json").read_text(encoding="utf-8"))["entity"]
EN = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))["entity"]

#: A few of the entities, one or more per platform, by (platform, key): the
#: ones an entry without display pages has, as the shared set-up makes it.
SOME = (
    ("sensor", "outdoor_temp"),
    ("sensor", "system_status"),
    ("sensor", "set_room_1"),
    ("sensor", "hp_model"),
    ("sensor", "last_start"),
    ("sensor", "active_overrides"),
    ("binary_sensor", "compressor_running"),
    ("number", "ctl_room_setpoint_1"),
    ("select", "ctl_dhw_mode"),
    ("button", "release_control"),
    ("event", "events"),
)

#: The device's name, the prefix of every entity id: "CTC " and the model.
PREFIX = "ctc_ecozenith_i255"


def _entry(hass) -> MockConfigEntry:
    return hass.config_entries.async_entries(DOMAIN)[0]


def _registry_entry(hass, platform: str, key: str) -> er.RegistryEntry:
    entity_id = _entity_id(hass, platform, key)
    item = er.async_get(hass).async_get(entity_id)
    assert item is not None
    return item


# ------------------------------------------------------- a new installation


async def test_a_new_installation_in_english_has_english_names_and_ids(hass):
    hass.config.language = "en"
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    for platform, key in SOME:
        item = _registry_entry(hass, platform, key)
        english = EN[platform][key]["name"]
        assert item.original_name == english, (platform, key)
        state = hass.states.get(item.entity_id)
        assert state is not None
        assert state.attributes["friendly_name"] == f"CTC {MODEL} {english}"
    assert _entity_id(hass, "sensor", "outdoor_temp") == f"sensor.{PREFIX}_outdoor_temperature"
    assert _entity_id(hass, "number", "ctl_room_setpoint_1") == (
        f"number.{PREFIX}_control_room_setpoint"
    )
    assert _entity_id(hass, "button", "release_control") == f"button.{PREFIX}_release_all_control"
    assert _entity_id(hass, "event", "events") == f"event.{PREFIX}_events"


@pytest.mark.parametrize(("language", "names"), [("en", EN), ("sv", SV)])
async def test_every_entity_of_the_entry_is_named_from_the_translations(hass, language, names):
    # None of them is left without a name, or with Home Assistant's name for
    # its device class, as one whose key the translations lacked would be.
    hass.config.language = language
    entry = await _set_up(hass)
    prefix = f"{DOMAIN}_{HOST}_"
    items = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    assert len(items) > 60
    for item in items:
        key = item.unique_id[len(prefix):]
        assert item.original_name == names[item.domain][key]["name"], item.entity_id
        assert item.translation_key == key, item.entity_id


async def test_a_new_installation_in_swedish_has_the_names_and_ids_it_always_had(hass):
    hass.config.language = "sv"
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    for platform, key in SOME:
        item = _registry_entry(hass, platform, key)
        assert item.original_name == SV[platform][key]["name"], (platform, key)
    # The ids an installation of 0.21.1 got, slugged from the Swedish names.
    assert _entity_id(hass, "sensor", "outdoor_temp") == f"sensor.{PREFIX}_utetemperatur"
    assert _entity_id(hass, "number", "ctl_room_setpoint_1") == f"number.{PREFIX}_styr_rumsborvarde"
    assert _entity_id(hass, "button", "release_control") == f"button.{PREFIX}_slapp_all_styrning"
    assert _entity_id(hass, "event", "events") == f"event.{PREFIX}_handelser"


async def test_a_dutch_home_assistant_gets_the_english_names(hass):
    # There is no Dutch translation, so Home Assistant falls back on English,
    # for the names as for the ids.
    hass.config.language = "nl"
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    for platform, key in SOME:
        item = _registry_entry(hass, platform, key)
        assert item.original_name == EN[platform][key]["name"], (platform, key)
    assert _entity_id(hass, "sensor", "outdoor_temp") == f"sensor.{PREFIX}_outdoor_temperature"


# ---------------------------------------------- an installation from before


async def _set_up_after_0_21(hass, given: dict[tuple[str, str], str] | None = None):
    """An entry whose registry holds the entities as 0.21.1 made them, then set up.

    0.21.1 set every name in code, in Swedish, so the registry has the ids
    slugged from them and the Swedish names as original names. ``given`` is a
    name an owner gave an entity, by (platform, key).
    """
    version = str((await async_get_integration(hass, DOMAIN)).version)
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{HOST}",
        title=f"{MODEL} ({HOST})",
        data={
            CONF_HOST: HOST,
            CONF_MODBUS_PORT: 502,
            CONF_WEB_PORT: 80,
            CONF_SLAVE: 1,
            "model": MODEL,
        },
        options={CONF_MENU_VERSION: version, CONF_IDENTITY: IDENTITY},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    before: dict[tuple[str, str], str] = {}
    for platform, key in SOME:
        swedish = SV[platform][key]["name"]
        item = registry.async_get_or_create(
            platform,
            DOMAIN,
            f"{DOMAIN}_{HOST}_{key}",
            suggested_object_id=f"{PREFIX}_{swedish}",
            config_entry=entry,
            original_name=swedish,
            has_entity_name=True,
        )
        if given and (platform, key) in given:
            registry.async_update_entity(item.entity_id, name=given[(platform, key)])
        before[(platform, key)] = item.entity_id
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, before


async def test_an_upgraded_installation_keeps_its_ids_and_gets_the_english_names(hass):
    hass.config.language = "en"
    entry, before = await _set_up_after_0_21(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert before[("sensor", "outdoor_temp")] == f"sensor.{PREFIX}_utetemperatur"
    for (platform, key), entity_id in before.items():
        assert _entity_id(hass, platform, key) == entity_id, (platform, key)
        item = er.async_get(hass).async_get(entity_id)
        english = EN[platform][key]["name"]
        assert item.original_name == english, (platform, key)
        assert item.name is None
        assert hass.states.get(entity_id).attributes["friendly_name"] == f"CTC {MODEL} {english}"


async def test_a_name_the_owner_gave_stays_through_the_upgrade(hass):
    hass.config.language = "en"
    given = {("sensor", "outdoor_temp"): "Ute vid garaget"}
    _entry, before = await _set_up_after_0_21(hass, given)
    entity_id = before[("sensor", "outdoor_temp")]
    item = er.async_get(hass).async_get(entity_id)
    assert item.name == "Ute vid garaget"
    assert item.original_name == "Outdoor temperature"
    assert hass.states.get(entity_id).attributes["friendly_name"] == "Ute vid garaget"


async def test_an_upgraded_installation_in_swedish_changes_nothing(hass):
    hass.config.language = "sv"
    _entry, before = await _set_up_after_0_21(hass)
    for (platform, key), entity_id in before.items():
        assert _entity_id(hass, platform, key) == entity_id
        item = er.async_get(hass).async_get(entity_id)
        assert item.original_name == SV[platform][key]["name"], (platform, key)


# ---------------------------------------------------- what stays Swedish


class RunningPump(FakeModbus):
    """A controller whose compressor runs for hot water, under the upper tank."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.registers.update({62005: 0, 62017: 33, 61500: 1})


async def test_states_and_options_stay_the_controllers_words_in_english(hass, stubs):
    from unittest.mock import patch

    hass.config.language = "en"
    with patch(f"custom_components.{DOMAIN}.CtcModbusClient", RunningPump):
        entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    # The binary reads the code behind the label, so it is on in English too.
    running = hass.states.get(_entity_id(hass, "binary_sensor", "compressor_running"))
    assert running.state == "on"
    assert running.attributes["friendly_name"].endswith("Compressor running")
    status = hass.states.get(_entity_id(hass, "sensor", "hp1_status"))
    assert status.state == "Till varmvatten"
    assert hass.states.get(_entity_id(hass, "sensor", "system_status")).state == "Värmepump övre"
    mode = hass.states.get(_entity_id(hass, "select", "ctl_dhw_mode"))
    assert mode.attributes["options"][0] == "Släpp styrningen"
    assert "Ekonomi" in mode.attributes["options"]
    assert mode.state == "Normal"


# ------------------------------------------------------------- the page


def _view(config: dict, path: str) -> dict:
    return next(view for view in config["views"] if view["path"] == path)


def _headings(view: dict) -> list[str]:
    return [section["cards"][0].get("heading") for section in view["sections"]]


def _items(view: dict) -> list[dict]:
    found = []
    for section in view["sections"]:
        for card in section["cards"][1:]:
            found.extend(card.get("items") or [])
            found.extend(row for row in card.get("rows") or [] if "entity" in row)
    return found


async def test_the_page_is_built_in_english_for_an_english_home_assistant(hass):
    hass.config.language = "en"
    await _set_up(hass)
    config = json.loads(json.dumps(dashboard.build_config(hass)))
    assert [view["title"] for view in config["views"]] == [
        "Overview", "Controls", "Performance", "All values",
    ]
    assert "Temperatures" in _headings(_view(config, "performance"))
    controls = _items(_view(config, "controls"))
    room_id = _entity_id(hass, "number", "ctl_room_setpoint_1")
    room = next(i for i in controls if i["entity"] == room_id)
    # Without the "Control" every control's name starts with, under a heading
    # that says it already.
    assert room["name"] == "Room setpoint"
    assert room["explanation"].startswith("The room temperature wanted for heating system 1.")
    assert room["source"] == "Modbus register 1010, volatile"
    outdoor = next(
        i for i in _items(_view(config, "values"))
        if i["entity"] == _entity_id(hass, "sensor", "outdoor_temp")
    )
    assert outdoor["name"] == "Outdoor temperature"
    assert outdoor["source"] == "Modbus register 62000"


async def test_the_page_is_built_in_swedish_for_a_swedish_home_assistant(hass):
    hass.config.language = "sv"
    await _set_up(hass)
    config = json.loads(json.dumps(dashboard.build_config(hass)))
    assert [view["title"] for view in config["views"]] == [
        "Översikt", "Styrning", "Prestanda", "Alla värden",
    ]
    controls = _items(_view(config, "controls"))
    room_id = _entity_id(hass, "number", "ctl_room_setpoint_1")
    room = next(i for i in controls if i["entity"] == room_id)
    assert room["name"] == "Rumsbörvärde"
    assert room["explanation"].startswith("Önskad rumstemperatur för värmesystem 1.")
    assert room["source"] == "Modbus-register 1010, flyktigt"


async def test_the_page_follows_home_assistant_not_the_panel(hass):
    # Every entry is made with the panel read in Swedish (CONF_LANGUAGE), and
    # that is how its menu and rows are read; the page's words are Home
    # Assistant's.
    hass.config.language = "en"
    await _set_up(hass)
    assert _entry(hass).options.get("language") in (None, 1)
    assert [pump["language"] for pump in dashboard._collect(hass)] == ["en"]
    hass.config.language = "sv"
    assert [pump["language"] for pump in dashboard._collect(hass)] == ["sv"]
    assert dashboard.page_language(hass) == "sv"
    hass.config.language = "nl"
    assert dashboard.page_language(hass) == "en"
