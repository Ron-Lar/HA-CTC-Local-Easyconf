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


def test_the_mac_decides_where_the_address_is_free():
    # The unit left its address and turned up at one no entry has.
    known = [keys.Known("a", HOST, "02:00:00:00:00:01"), keys.Known("b", "192.0.2.10", "02:00:00:00:00:02")]
    assert keys.match_discovery(known, MOVED_TO, "020000000002") == (keys.MOVED, "b")
    assert keys.held_back(known, MOVED_TO, "020000000002") is None


def test_no_entry_is_moved_to_an_address_another_entry_has():
    # Another unit's entry stands at the address the moved unit turned up at:
    # moving there would leave two entries on one address, two Modbus clients
    # against the controller's one place. The entry stays, and is named.
    known = [keys.Known("a", HOST, "02:00:00:00:00:01"), keys.Known("b", MOVED_TO, "02:00:00:00:00:02")]
    assert keys.match_discovery(known, HOST, "020000000002") == (keys.KNOWN, "a")
    assert keys.held_back(known, HOST, "020000000002") == "b"


# The heat pump set up twice: A waits at the address the unit left (setup
# retry holds no Modbus session, so B's probe got through), and B was added
# at the address it went to and has read the same MAC. Or a 0.18.0 that
# offered the moved unit as new, and an owner who took the offer.
TWICE = [keys.Known("A", HOST, "02:00:00:00:00:01"), keys.Known("B", MOVED_TO, "02:00:00:00:00:01")]


@pytest.mark.parametrize("known", [TWICE, TWICE[::-1]], ids=["oldest-first", "newest-first"])
def test_the_same_heat_pump_set_up_twice_is_never_put_on_one_address(known):
    assert keys.match_discovery(known, MOVED_TO, "020000000001") == (keys.KNOWN, "B")
    assert keys.held_back(known, MOVED_TO, "020000000001") == "A"
    # And from the old address, should the unit go back: A is where it is.
    assert keys.match_discovery(known, HOST, "020000000001") == (keys.KNOWN, "A")
    assert keys.held_back(known, HOST, "020000000001") == "B"


@pytest.mark.parametrize("other_mac", [None, "02:00:00:00:00:02"], ids=["no-mac", "another-mac"])
def test_an_entry_at_the_address_keeps_it_whatever_its_mac(other_mac):
    known = [keys.Known("A", HOST, "02:00:00:00:00:01"), keys.Known("B", MOVED_TO, other_mac)]
    for order in (known, known[::-1]):
        assert keys.match_discovery(order, MOVED_TO, "020000000001") == (keys.KNOWN, "B")
        assert keys.held_back(order, MOVED_TO, "020000000001") == "A"


def test_nothing_is_held_back_where_nothing_stands_in_the_way():
    assert keys.held_back(KNOWN, HOST, "020000000001") is None
    assert keys.held_back(KNOWN, MOVED_TO, "020000000001") is None
    assert keys.held_back(KNOWN, "192.0.2.10", None) is None


@pytest.mark.parametrize("host", ["ctc8489.lan", "CTC8489", "varmepump.hemma.se", "fd00::1234"])
def test_an_entry_set_up_by_name_or_ipv6_is_where_it_is(host):
    # DNS follows the unit, and DHCP hands out no IPv6 address: the lease's
    # address is no news, and moving the entry there would reload it with a
    # second client knocking on the same pump.
    known = [keys.Known("e1", host, "02:00:00:00:84:89")]
    assert keys.match_discovery(known, "192.0.2.77", "020000008489") == (keys.KNOWN, "e1")


def test_only_an_address_of_the_leases_kind_follows_the_lease():
    assert keys.leased_address(HOST, MOVED_TO)
    assert not keys.leased_address("ctc8489.lan", MOVED_TO)
    assert not keys.leased_address("fd00::1234", MOVED_TO)
    assert keys.leased_address("fd00::1234", "fd00::5678")
    assert not keys.leased_address(None, MOVED_TO)
    assert not keys.leased_address("", MOVED_TO)
    assert not keys.leased_address(HOST, "inte en adress")


# ------------------------------------------------- a key for a new entry


def test_a_new_entry_is_known_by_its_address_where_it_is_free():
    assert keys.free_key(HOST, []) == HOST
    assert keys.free_key(HOST, [MOVED_TO, "ctc8489.lan"]) == HOST


def test_a_new_entry_at_an_address_a_moved_entry_was_created_with_gets_its_own_key():
    assert keys.free_key(HOST, [HOST]) == f"{HOST}#2"
    assert keys.free_key(HOST, [HOST, f"{HOST}#2"]) == f"{HOST}#3"
    # Neither heat pump's unique_ids begin with the other's prefix.
    first, second = keys.unique_prefix(HOST), keys.unique_prefix(keys.free_key(HOST, [HOST]))
    assert not second.startswith(first) and not first.startswith(second)


# ------------------------------------------------- the settle after a move


def test_a_connection_at_the_new_address_waits_out_the_close_at_the_old(modbus_api, monkeypatch):
    # The move reloads the entry: the old client closes on the old address
    # after the move is said, and the new one is the same pump.
    monkeypatch.setattr(modbus_api, "_CLOSED_AT", {})
    monkeypatch.setattr(modbus_api, "_MOVED_FROM", {})
    modbus_api.note_moved("ctc8489.lan", MOVED_TO, 502)
    assert modbus_api.settle_wait(MOVED_TO, 502, 1000.0) == 0.0
    modbus_api.note_close("ctc8489.lan", 502, 1000.0)
    assert modbus_api.settle_wait(MOVED_TO, 502, 1000.0) == modbus_api.CLOSE_SETTLE
    assert modbus_api.settle_wait(MOVED_TO, 502, 1004.0) == modbus_api.CLOSE_SETTLE - 4.0
    # The newest close of the two counts.
    modbus_api.note_close(MOVED_TO, 502, 995.0)
    assert modbus_api.settle_wait(MOVED_TO, 502, 1004.0) == modbus_api.CLOSE_SETTLE - 4.0
    # Another port, and an address nothing moved to, are not held back.
    assert modbus_api.settle_wait(MOVED_TO, 503, 1000.0) == 0.0
    assert modbus_api.settle_wait("192.0.2.99", 502, 1000.0) == 0.0
    # Moving nowhere says nothing.
    modbus_api.note_moved(HOST, HOST, 502)
    assert (HOST, 502) not in modbus_api._MOVED_FROM


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
    # The address, or where a moved entry still has its key, keys.free_key's.
    assert "CONF_DEVICE_KEY: self._key or self._host," in flow
    assert "self._key = self._free_key(self._host)" in flow
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
