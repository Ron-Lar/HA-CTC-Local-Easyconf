"""The three actions without Home Assistant: their texts, their wiring and their rules (R76).

The actions themselves run under a real core in test_homeassistant_services.py.
Here is what can be read without one: hassfest's demands on services.yaml,
strings.json and the translations, the one-time registration from async_setup,
the key an entity's unique_id carries back to its control register, and what
cop_sample answers with.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import re
from types import SimpleNamespace

from conftest import COMPONENT, load

TEXTS = ("strings.json", "translations/en.json", "translations/sv.json")
NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
DEVICE = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}


def _json(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))


def _services_yaml() -> dict[str, list[str]]:
    """Each action in services.yaml with its fields, read without a YAML library.

    The file is flat and written by hand: an action at column 0, ``fields:``
    under it, and each field two levels in.
    """
    actions: dict[str, list[str]] = {}
    current = None
    for line in (COMPONENT / "services.yaml").read_text(encoding="utf-8").splitlines():
        if re.fullmatch(r"[a-z_]+:", line):
            current = line[:-1]
            actions[current] = []
        elif current and re.fullmatch(r"    [a-z_]+:", line):
            actions[current].append(line.strip()[:-1])
    return actions


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def run(coro):
    return asyncio.run(coro)


# ------------------------------------------------------------- hassfest


def test_the_three_actions_and_their_fields():
    assert _services_yaml() == {
        "release": ["entity_id"],
        "reread_menu": ["config_entry_id"],
        "cop_sample": ["config_entry_id"],
    }
    # The card (R49) calls release with the field entity_id; the names are a contract.
    services = _source("services.py")
    for name in ("release", "reread_menu", "cop_sample"):
        assert f'"{name}"' in services, name


def test_every_action_and_field_has_a_name_and_a_description_in_every_language():
    actions = _services_yaml()
    for text in TEXTS:
        services = _json(text)["services"]
        assert set(services) == set(actions), text
        for action, fields in actions.items():
            assert services[action]["name"] and services[action]["description"], (text, action)
            assert set(services[action]["fields"]) == set(fields), (text, action)
            for field in fields:
                described = services[action]["fields"][field]
                assert described["name"] and described["description"], (text, action, field)


def test_the_english_texts_are_the_strings_and_swedish_has_every_key():
    strings = _json("strings.json")
    english = _json("translations/en.json")
    swedish = _json("translations/sv.json")
    for section in ("services", "exceptions"):
        assert strings[section] == english[section], section
        assert set(swedish[section]) == set(strings[section]), section


def test_every_refusal_the_actions_raise_has_a_text_with_its_placeholders():
    services = _source("services.py")
    raised = dict(re.findall(r'_(?:refuse|failed)\(\s*"([a-z_]+)"((?:, [a-z_]+=[^,)]+)*)',
                             services))
    assert set(raised) == {
        "not_a_control", "unknown_entry", "entry_not_loaded", "no_display",
        "no_display_loaded", "panel_busy", "menu_not_read", "menu_busy_and_not_read",
        "menu_partly_read", "no_counters", "no_counters_loaded",
    }
    for text in TEXTS:
        exceptions = _json(text)["exceptions"]
        for key, arguments in raised.items():
            message = exceptions[key]["message"]
            wanted = set(re.findall(r"([a-z_]+)=", arguments))
            assert set(re.findall(r"\{([a-z_]+)\}", message)) == wanted, (text, key)


def test_the_swedish_texts_use_no_dash_as_punctuation():
    swedish = _json("translations/sv.json")
    words = []
    for action in swedish["services"].values():
        words += [action["name"], action["description"]]
        for field in action["fields"].values():
            words += [field["name"], field["description"]]
    words += [item["message"] for item in swedish["exceptions"].values()]
    for word in words:
        assert "—" not in word and "–" not in word and " - " not in word, word


def test_async_setup_registers_the_actions_once_and_takes_no_yaml():
    init = _source("__init__.py")
    assert "CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)" in init
    setup = init.split("async def async_setup(")[1].split("\nasync def ")[0]
    assert "async_register_services(hass)" in setup
    services = _source("services.py")
    register = services.split("def async_register_services(")[1]
    assert register.index("has_service(DOMAIN, SERVICE_RELEASE)") < (
        register.index("async_register(")
    )
    assert "supports_response=SupportsResponse.OPTIONAL" in register


def test_release_writes_nothing_and_reread_waits_for_no_panel():
    services = _source("services.py")
    release = services.split("async def _release(")[1].split("async def ")[0]
    assert "async_set(register.address, None)" in release
    assert "async_write" not in services, "åtgärderna når aldrig Modbus-klienten själva"
    init = _source("__init__.py")
    now = init.split("async def async_reread_menu_now(")[1].split("\nasync def ")[0]
    # The options form's reading: the panel lock looked at, never waited for.
    assert "await async_rescan_pages(client)" in now
    assert "menu_read_from_the_options(" in now
    assert now.index("not reading.complete") < now.index("_menu_changes(")


def test_the_action_writes_options_worked_out_under_the_lock_and_only_to_its_own_runtime():
    # F1.3: the options from the moment of the write, and nothing written to
    # an entry that was reloaded or unloaded beneath the walk.
    init = _source("__init__.py")
    now = init.split("async def async_reread_menu_now(")[1].split("\nasync def ")[0]
    write = now.split("async with client.panel:")[1]
    assert "_menu_changes(" not in now.split("async with client.panel:")[0]
    assert write.index("_still_loaded_with(entry, runtime)") < write.index("_menu_changes(")
    assert write.rindex("_still_loaded_with(entry, runtime)") > write.index("async_flush()")
    still = init.split("def _still_loaded_with(")[1].split("\nasync def ")[0]
    assert "ConfigEntryState.LOADED" in still and "is runtime" in still


def test_the_panel_lock_is_the_entrys_and_outlives_a_reload():
    init = _source("__init__.py")
    setup = init.split("async def async_setup_entry(")[1].split("\nasync def ")[0]
    assert "web_client.panel = panel_lock(entry.entry_id)" in setup
    assert setup.index("web_client.panel = panel_lock(") < setup.index("CtcWebCoordinator(")
    remove = init.split("async def async_remove_entry(")[1]
    assert "_PANEL_LOCKS.pop(entry.entry_id" in remove
    unload = init.split("async def async_unload_entry(")[1].split("\nasync def ")[0]
    assert "_PANEL_LOCKS" not in unload, "en omladdning behåller låset"


def test_a_catch_up_queued_behind_another_reading_walks_nothing():
    # F1.1: the last attempt read before the lock is taken and compared once it is.
    init = _source("__init__.py")
    body = init.split("async def _async_reread_menu(")[1].split("\ndef ")[0]
    taken = body.index("async with client.panel:")
    assert body.index("_MENU_LAST.get(entry.entry_id)") < taken
    after = body[taken:]
    assert after.index("_MENU_LAST.get(entry.entry_id) != attempted") < after.index(
        "async_discover_pages("
    )
    assert "_MENU_TRIES" not in after.split("async_discover_pages(")[0]


def test_an_answer_over_several_pumps_names_every_kind_and_says_nothing_changed_only_when_so():
    services = _source("services.py")
    handler = services.split("async def _reread_menu(")[1].split("async def _cop_sample(")[0]
    # Partly read is decided before any of the answers that say nothing changed.
    partly = handler.index('_failed("menu_partly_read"')
    for key in ("menu_busy_and_not_read", "panel_busy", "menu_not_read"):
        assert partly < handler.index(f'_failed("{key}"'), key
    english = _json("strings.json")["exceptions"]
    swedish = _json("translations/sv.json")["exceptions"]
    assert "Nothing has been changed" not in english["menu_partly_read"]["message"]
    assert "Inget har ändrats" not in swedish["menu_partly_read"]["message"]
    assert "Inget har ändrats" in swedish["menu_busy_and_not_read"]["message"]
    # An empty list is a count, not a word in one language.
    assert re.search(r'NONE_LISTED = "0"', services)


# ------------------------------------------------------- the key and the register


def test_a_unique_id_gives_back_the_key_it_was_built_from():
    keys = load("keys")
    assert keys.key_of(keys.unique_id(DEVICE, "ctl_room_setpoint_1"), DEVICE) == (
        "ctl_room_setpoint_1"
    )
    other = {"identifiers": {("ctc_ecozenith", "192.0.2.56")}}
    assert keys.key_of(keys.unique_id(other, "ctl_room_setpoint_1"), DEVICE) is None
    assert keys.key_of(None, DEVICE) is None
    assert keys.key_of(keys.unique_prefix("192.0.2.55"), DEVICE) is None


def test_a_moved_entry_is_known_by_its_key_and_not_its_address():
    keys = load("keys")
    # Created at .55, moved to .60: the identifier keeps the key it was created with.
    moved = {"identifiers": {("ctc_ecozenith", "192.0.2.55")}}
    assert keys.key_of("ctc_ecozenith_192.0.2.55_ctl_dhw_mode", moved) == "ctl_dhw_mode"
    assert keys.key_of("ctc_ecozenith_192.0.2.60_ctl_dhw_mode", moved) is None


def test_the_registers_by_key_are_exactly_the_control_tables(const):
    tables = const.CONTROL_NUMBERS + const.CONTROL_SELECTS
    assert {key: register.address for key, register in const.CONTROL_BY_KEY.items()} == {
        register.key: register.address for register in tables
    }
    assert {register.address for register in const.CONTROL_BY_KEY.values()} == (
        const.CONTROL_ADDRESSES
    )


# ------------------------------------------------------------- the sample


class FakeStore:
    def __init__(self) -> None:
        self.data = None

    async def async_load(self):
        return self.data

    async def async_save(self, data) -> None:
        self.data = data


def _runtime(cop, *, read=NOW - timedelta(minutes=20), fresh=True, energy_in=True):
    data = {"out": 22510.0, "in": 9120.0}
    web = SimpleNamespace(
        data=data,
        last_read=lambda key: read if key in data else None,
        is_fresh=lambda key: fresh,
    )
    return SimpleNamespace(
        cop=cop.CopTracker(FakeStore()),
        web=web,
        energy_out=SimpleNamespace(key="out"),
        energy_in=SimpleNamespace(key="in") if energy_in else None,
        consumption_snapshot=SimpleNamespace(value=9121.0),
    )


def test_the_answer_is_the_counters_their_moment_and_whether_a_sample_was_kept(cop):
    runtime = _runtime(cop)
    read = NOW - timedelta(minutes=20)
    assert cop.sample_summary(runtime, False) == {
        "recorded": False,
        "heat_out_kwh": 22510.0,
        "energy_in_kwh": 9120.0,
        "energy_in_from": "display",
        "read_at": read.isoformat(timespec="seconds"),
        "last_sample_at": None,
    }
    run(runtime.cop.async_record(22510.0, 9120.0, now=read))
    answer = cop.sample_summary(runtime, True)
    assert answer["recorded"] is True
    assert answer["last_sample_at"] == read.isoformat(timespec="seconds")
    json.dumps(answer)  # plain JSON, as a response has to be


def test_a_stale_counter_page_gives_no_moment_and_the_modbus_route_says_so(cop):
    stale = cop.sample_summary(_runtime(cop, fresh=False), False)
    assert stale["read_at"] is None
    modbus = cop.sample_summary(_runtime(cop, energy_in=False), False)
    assert modbus["energy_in_from"] == "modbus"
    assert modbus["energy_in_kwh"] == 9121.0
