"""The public repository names no network, no machine and no person's computer (R69, R74).

The widget fixtures were captured over ssh from the two houses' Home Assistant
machines, and the capture used to end with the ssh session's own closing line,
which named the machine. Tests use the documentation range 192.0.2.0/24 instead,
the one RFC 5737 sets aside for examples, so nothing in here routes anywhere.

The rules below are general and name nothing of the houses, since a list of
what to look for would publish what it is looking for: hashing a four-digit
sequence number hides nothing, as anyone can try all ten thousand. So the
tests and the integration's own files may carry

- a private IPv4 address (10/8, 172.16/12, 192.168/16) only where it is one of
  the made-up ones listed here;
- a MAC address only as one of the made-up 02:00:00:00:00:0x, a locally
  administered address that is nobody's, written with separators or as twelve
  bare digits;
- the display's host name, CTC and four digits, only as the made-up CTC0001:
  the four digits are the serial number's sequence group;
- a serial number only with the made-up sequence group 0001, as in the tests'
  720825400001, whose product and week groups are those of a real production
  run (they are what the report sends) and whose sequence number, the group
  that identifies a machine, is invented; and CTC's own example from its guide
  to the serial number;
- no local path, which names a user and a machine layout.

What the rules cannot see is a public address, a host name of somebody's own
DNS or a serial number written some other way, so a contributor still takes
their own out of a fixture by hand.
"""

from __future__ import annotations

import ipaddress
import json
import pathlib
import re

import pytest

from conftest import COMPONENT, FIXTURES, ROOT

TESTS = pathlib.Path(__file__).resolve().parent

WIDGET_FIXTURES = sorted(FIXTURES.glob("widgets_*.json"))


def test_the_widget_fixtures_are_the_four_real_pages():
    assert [p.name for p in WIDGET_FIXTURES] == [
        "widgets_i255_118.json",
        "widgets_i255_128.json",
        "widgets_i550_136.json",
        "widgets_i550_138.json",
    ]


@pytest.mark.parametrize("path", WIDGET_FIXTURES, ids=lambda p: p.stem)
def test_a_widget_fixture_holds_the_screen_and_its_widgets_only(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == {"screen", "widgets"}, "the raw values were ssh noise and are gone"
    assert isinstance(data["screen"], int)
    assert data["widgets"] and all(isinstance(w["index"], int) for w in data["widgets"])


# ------------------------------------------------------------ what is looked for


#: Private addresses the tests use for networks that belong to nobody in
#: particular: subnets the discovery picks between, a stray address in a
#: diagnostics dump, a display that is not there. A new one goes on this list
#: only when it is made up.
MADE_UP_PRIVATE_ADDRESSES = frozenset(
    {
        "10.1.2.3",
        "172.16.4.0",
        "172.16.4.9",
        "172.20.30.0",
        "172.20.30.40",
        "192.168.1.0",
        "192.168.1.4",
        "192.168.1.9",
        "192.168.1.55",
        "192.168.1.155",
        "192.168.2.0",
        "192.168.2.9",
        "192.168.3.3",
        "192.168.6.0",
        "192.168.6.10",
        "192.168.7.0",
        "192.168.7.20",
        "192.168.9.5",
    }
)

#: Four dotted numbers that are not part of a longer dotted run, such as a
#: version number with more parts.
IPV4 = re.compile(r"(?<![\d.])\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?!\.?\d)")

#: The made-up MAC addresses, as twelve hex digits: 02:00:00:00:00:00 to
#: 02:00:00:00:00:0f.
MADE_UP_MACS = frozenset(f"02000000000{digit}" for digit in "0123456789abcdef")

#: A MAC address with colons or dashes between the bytes, or in the dotted
#: groups of four some routers print.
MAC_WRITTEN_OUT = re.compile(
    r"(?<![\w:.-])(?:[0-9a-f]{2}([:-])(?:[0-9a-f]{2}\1){4}[0-9a-f]{2}"
    r"|[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4})(?![\w:.-])",
    re.IGNORECASE,
)

#: Twelve hex digits standing alone: a MAC without separators, the way
#: Home Assistant's DHCP watcher hands it over, or, when all of them are
#: decimal, a serial number. The last group of a UUID stands after a dash and
#: is neither.
TWELVE_BARE = re.compile(r"(?<![\w.:-])[0-9a-f]{12}(?![\w.:-])", re.IGNORECASE)

#: A serial number written in its three groups, with spaces or dashes.
SERIAL_IN_GROUPS = re.compile(r"(?<![\w-])\d{4}([ -])\d{4}\1\d{4}(?![\w-])")

#: The only sequence group a serial number in here may have.
MADE_UP_SEQUENCE = "0001"

#: CTC's own example in its guide to the serial number, an EcoAir 510M from
#: 2017 week 12: published by the maker, so it names no machine of ours.
PUBLISHED_SERIALS = frozenset({"731217120719"})

#: The display's host name, which ends in the serial number's sequence group.
DISPLAY_HOST_NAME = re.compile(r"(?<![a-z])ctc(\d{4})(?!\d)", re.IGNORECASE)

#: The line ssh prints when the capture's session ends, naming the machine.
SSH_NOISE = re.compile(r"Connection to \S+ closed")

#: A path on somebody's own computer.
LOCAL_PATH = re.compile(r"/Users/|/home/")


def _private(address: str) -> bool:
    try:
        first, second, _, _ = ipaddress.IPv4Address(address).packed
    except ValueError:
        return False
    return first == 10 or (first == 172 and 16 <= second <= 31) or (first, second) == (192, 168)


def _made_up_serial(digits: str) -> bool:
    return digits.endswith(MADE_UP_SEQUENCE) or digits in PUBLISHED_SERIALS


def what_names_a_machine(text: str) -> list[str]:
    """Everything in ``text`` that may name a network, a machine or a computer."""
    found = [
        f"the private address {address}"
        for address in IPV4.findall(text)
        if _private(address) and address not in MADE_UP_PRIVATE_ADDRESSES
    ]
    for match in MAC_WRITTEN_OUT.finditer(text):
        if re.sub(r"[^0-9a-f]", "", match.group(0).lower()) not in MADE_UP_MACS:
            found.append(f"the MAC address {match.group(0)}")
    for token in TWELVE_BARE.findall(text):
        if token.lower() in MADE_UP_MACS:
            continue
        if token.isdigit():
            if not _made_up_serial(token):
                found.append(f"the serial number or MAC address {token}")
        else:
            found.append(f"the MAC address {token}")
    for match in SERIAL_IN_GROUPS.finditer(text):
        if not _made_up_serial(re.sub(r"\D", "", match.group(0))):
            found.append(f"the serial number {match.group(0)}")
    found += [
        f"the host name CTC{digits}"
        for digits in DISPLAY_HOST_NAME.findall(text)
        if digits != MADE_UP_SEQUENCE
    ]
    if SSH_NOISE.search(text):
        found.append("the ssh session's closing line")
    if LOCAL_PATH.search(text):
        found.append("a path from somebody's own computer")
    return found


# -------------------------------------------------------------- the rules hold


@pytest.mark.parametrize(
    "text",
    [
        "192.168.77.12",
        "host: 10.20.30.41",
        "172.31.0.9:502",
        "02:00:00:00:12:34",
        "A4-B1-C2-D3-E4-F5",
        "a4b1.c2d3.e4f5",
        '"macaddress": "a4b1c2d3e4f5"',
        "020000001234",
        "serienummer 731299990002",
        "7208 2540 1234",
        "ctc1234.lan",
        "CTC9876",
        "Connection to example closed.",
        "/home/somebody/ha",
    ],
)
def test_the_rules_catch_what_a_capture_brings_in(text):
    assert what_names_a_machine(text), text


@pytest.mark.parametrize(
    "text",
    [
        "192.0.2.55",
        "192.168.1.0/24",
        "10.1.2.3",
        "8.8.8.8",
        "version 2026.10.1.2",
        "02:00:00:00:00:01",
        "02-00-00-00-00-02",
        "0200.0000.0001",
        "020000000001",
        "720825400001",
        "7208-2540-0001",
        "7312-1712-0719",
        "install_id 0f8fad5b-d9cb-469f-a165-70867728950e",
        "ctc0001.local",
        "CTC0001",
        "ctc_ecozenith",
        "fd00::1234",
    ],
)
def test_the_rules_let_the_made_up_values_through(text):
    assert not what_names_a_machine(text), text


def test_the_made_up_addresses_are_private_and_none_begins_with_10_0():
    # Nothing in 10.0 is made up here, so an address from it never passes.
    assert all(_private(a) for a in MADE_UP_PRIVATE_ADDRESSES)
    assert not [a for a in MADE_UP_PRIVATE_ADDRESSES if a.startswith("10.0.")]


# --------------------------------------------------------------- the scan


#: Everything under tests/ and in the integration that is text, except this
#: file, which spells out what it is looking for.
SCANNED = sorted(
    p
    for root in (TESTS, COMPONENT)
    for p in root.rglob("*")
    if p.is_file()
    and p.suffix in (".py", ".json", ".txt", ".js", ".yaml")
    and p.resolve() != pathlib.Path(__file__).resolve()
)


def test_the_scan_reaches_the_tests_and_the_integration():
    names = {p.name for p in SCANNED}
    assert {"keys.py", "diagnostics_data.py", "manifest.json", "ctc-ecozenith-card.js"} <= names
    assert {"test_device_key.py", "widgets_i255_118.json"} <= names


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: str(p.relative_to(ROOT)))
def test_nothing_in_the_repository_names_a_machine(path):
    text = path.read_text(encoding="utf-8", errors="replace")
    assert not what_names_a_machine(text), f"{path.name} carries {what_names_a_machine(text)}"
