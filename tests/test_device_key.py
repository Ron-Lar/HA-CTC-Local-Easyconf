"""The device and every entity are known by a key that stays when the address moves (R20).

An entry used to be known by its address alone: (domain, host) for the device
and "<domain>_<host>_<key>" for every entity, built anew in eleven places. A
new DHCP lease then gave Home Assistant a second device beside the first, which
stood unavailable with the history. The key is written into a new entry's data,
an entry from before reads its address in its place, the DHCP flow pins the key
before it moves the address, and every unique_id is built in keys.py.
"""

from __future__ import annotations

import re

import pytest

from conftest import COMPONENT, load

keys = load("keys")
const = load("const")

HOST = "192.0.2.55"
MOVED_TO = "192.0.2.77"


def _device(key: str) -> dict:
    return {"identifiers": {(const.DOMAIN, key)}, "name": "CTC EcoZenith i255"}


# --------------------------------------------------------------- the key


def test_an_entry_from_before_the_key_is_known_by_its_address():
    assert keys.device_key({"host": HOST}) == HOST


def test_a_written_key_wins_over_the_address():
    assert keys.device_key({"host": MOVED_TO, const.CONF_DEVICE_KEY: HOST}) == HOST


def test_an_empty_key_is_no_key():
    assert keys.device_key({"host": HOST, const.CONF_DEVICE_KEY: ""}) == HOST


def test_the_unique_ids_of_an_existing_installation_do_not_move():
    # What every entity has been known by since the first release.
    device = _device(keys.device_key({"host": HOST}))
    assert keys.unique_id(device, "outdoor_temp") == f"ctc_ecozenith_{HOST}_outdoor_temp"
    assert keys.prefix_of(device) == f"ctc_ecozenith_{HOST}_"
    assert keys.unique_prefix(HOST) == f"ctc_ecozenith_{HOST}_"


def test_a_moved_entry_keeps_every_unique_id():
    before = {"host": HOST, "model": "EcoZenith i255"}
    after = keys.moved_data(before, MOVED_TO)
    assert after["host"] == MOVED_TO
    assert after[const.CONF_DEVICE_KEY] == HOST, "nyckeln skrivs innan adressen flyttas"
    assert after["model"] == "EcoZenith i255"
    assert keys.unique_id(_device(keys.device_key(after)), "outdoor_temp") == (
        keys.unique_id(_device(keys.device_key(before)), "outdoor_temp")
    )
    # And a second move keeps the first key, not the address in between.
    again = keys.moved_data(after, "192.0.2.99")
    assert again[const.CONF_DEVICE_KEY] == HOST and again["host"] == "192.0.2.99"


def test_the_title_follows_the_address_only_where_set_up_wrote_it():
    assert keys.moved_title(f"EcoZenith i255 ({HOST})", HOST, MOVED_TO) == f"EcoZenith i255 ({MOVED_TO})"
    assert keys.moved_title("Pannrummet", HOST, MOVED_TO) == "Pannrummet"
    assert keys.moved_title(f"EcoZenith i255 ({HOST}) gamla", HOST, MOVED_TO) == (
        f"EcoZenith i255 ({HOST}) gamla"
    )
    assert keys.moved_title("EcoZenith i255 ()", "", MOVED_TO) == "EcoZenith i255 ()"


# ------------------------------------------------------------------ the MAC


@pytest.mark.parametrize(
    "text",
    ["02:00:00:00:00:01", "02-00-00-00-00-01", "020000000001", "0200.0000.0001", "02:00:00:00:00:01".upper()],
)
def test_every_spelling_of_a_mac_is_the_same_mac(text):
    assert keys.mac_hex(text) == "020000000001"
    assert keys.mac_address(text) == "02:00:00:00:00:01"


@pytest.mark.parametrize("text", [None, "", "02:00:00:00:00", "inte en mac", 20000000001])
def test_what_is_not_a_mac_is_none(text):
    assert keys.mac_hex(text) is None
    assert keys.mac_address(text) is None


# --------------------------------------------------------- the DHCP verdict


KNOWN = [
    keys.Known("vsh", HOST, "02:00:00:00:00:01"),
    keys.Known("pt", "192.0.2.10", None),
]


def test_a_known_mac_at_a_new_address_has_moved():
    assert keys.match_discovery(KNOWN, MOVED_TO, "020000000001") == (keys.MOVED, "vsh")


def test_a_known_mac_at_its_own_address_is_known():
    assert keys.match_discovery(KNOWN, HOST, "02:00:00:00:00:01") == (keys.KNOWN, "vsh")


def test_an_entry_without_a_mac_is_known_by_its_address_alone():
    assert keys.match_discovery(KNOWN, "192.0.2.10", "020000000002") == (keys.KNOWN, "pt")


def test_an_unknown_mac_at_an_unknown_address_is_new():
    assert keys.match_discovery(KNOWN, MOVED_TO, "020000000002") == (keys.NEW, None)
    assert keys.match_discovery(KNOWN, MOVED_TO, None) == (keys.NEW, None)
    assert keys.match_discovery([], HOST, "020000000001") == (keys.NEW, None)


def test_the_mac_decides_before_the_address():
    # Another unit took the moved one's old address: the MAC says which is which.
    known = [keys.Known("a", HOST, "02:00:00:00:00:01"), keys.Known("b", MOVED_TO, "02:00:00:00:00:02")]
    assert keys.match_discovery(known, HOST, "020000000002") == (keys.MOVED, "b")


# ------------------------------------------------------- one place for all


def test_no_platform_builds_a_unique_id_of_its_own():
    """Every unique_id and every prefix is built in keys.py, nowhere else."""
    for path in sorted(COMPONENT.glob("*.py")):
        if path.name == "keys.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert '["identifiers"]))[1]' not in source, path.name
        # The entry's own unique_id, "<domain>_<host>", is not an entity's.
        assert not re.search(r"\{DOMAIN\}_\{host\}_", source), path.name


def test_the_device_is_identified_by_the_key_and_new_entries_write_it():
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "identifiers={(DOMAIN, key)}" in init
    assert "key = device_key(entry.data)" in init
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert "CONF_DEVICE_KEY: self._host," in flow
    # The unique_id abort takes no address back: a moved entry keeps its own.
    assert "updates={CONF_HOST" not in flow


def test_a_known_unit_wakes_an_entry_waiting_to_be_set_up_again():
    """The DHCP flow aborts before Home Assistant's own rule, so it wakes the entry itself.

    Home Assistant tries an entry in setup_retry at once when a discovery
    finds its unique_id; the flow recognises a unit by its MAC or address and
    aborts before that, the unit where it is as well as one that has moved.
    """
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    dhcp = flow.split("async def async_step_dhcp(")[1].split("\n    async def ")[0]
    assert "elif verdict == KNOWN and entry_id is not None:\n            self._async_wake_entry(entry_id)" in dhcp
    assert dhcp.index("self._async_wake_entry(entry_id)") < dhcp.index("if verdict != NEW:")
    move = flow.split("def _async_move_entry(")[1].split("\n    @callback")[0]
    assert "self._async_wake_entry(entry.entry_id)" in move
    wake = flow.split("def _async_wake_entry(")[1].split("\n    async def ")[0]
    assert "entry.state is config_entries.ConfigEntryState.SETUP_RETRY" in wake
    assert "async_schedule_reload(entry_id)" in wake


def test_the_manifest_asks_for_registered_devices():
    import json

    manifest = json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))
    assert {"registered_devices": True} in manifest["dhcp"]
    assert {"macaddress": "020000*"} in manifest["dhcp"]
