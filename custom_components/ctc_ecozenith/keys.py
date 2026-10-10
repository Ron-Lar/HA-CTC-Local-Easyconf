"""What a heat pump and its entities are known by, built in one place (roadmap R20).

An entry used to be known by its address and nothing else: the device's
identifier was (domain, host) and every entity's unique_id was
"<domain>_<host>_<key>". The address is the one thing about a heat pump that
can change under it, a new DHCP lease or a rebuilt network, and when it did
Home Assistant offered a second device while the first stood unavailable with
all its history.

So an entry carries a device key of its own, written into its data when it is
created and never changed afterwards. It is the address the entry was created
with: that keeps every unique_id an existing installation has exactly as it
was, and an entry from before the key existed reads its address in its place,
which comes to the same. When the DHCP flow then finds the unit at a new
address by its MAC, it moves the address and pins the key first (moved_data),
so the device and every entity stay what they were. An entry set up with a
host name is never moved, since the name follows the unit already
(leased_address), and a new entry at an address a moved entry was created
with is given a key of its own (free_key).

A display row's key follows the same thought (roadmap L2). It used to be built
from the row's name, so every time the parser read a name better, "Energi
el/30 dagar" where the row had passed for another before, the row got a new
key and its entity was left behind with a twin beside it. Now the key is where
the row stands: the page, the screen and the first variable it reads,
"p30_s128_v22". The name is free to improve. The first reading of the menu
after the change pairs every row of the stored menu with the row at the same
place in the new one (migrate_keys), and each new row carries the key it had
(with_previous_keys); set-up then moves the entity's unique_id and the stored
values over, and leaves the entity id alone.

Free of Home Assistant, like rows.py and seen.py: the rules are tested on their
own, and the platforms, the page and the registry tidy-up all ask here.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping

from .const import CONF_DEVICE_KEY, DOMAIN, SlowPage, SlowValue

#: Home Assistant's own name for the address in an entry's data (CONF_HOST),
#: spelled out so this module needs nothing from Home Assistant.
CONF_HOST = "host"

_NOT_HEX = re.compile(r"[^0-9a-f]")


def device_key(data: Mapping[str, Any]) -> str:
    """The key an entry's device and entities are known by.

    The one written at creation, or for an entry from before there was one,
    its address, which is what that entry's device and entities were built
    from. An entry that has moved always has the key written: moved_data
    pins it before the address changes.
    """
    key = data.get(CONF_DEVICE_KEY)
    if isinstance(key, str) and key:
        return key
    return str(data[CONF_HOST])


def unique_prefix(key: str) -> str:
    """What every unique_id of one heat pump begins with."""
    return f"{DOMAIN}_{key}_"


def prefix_of(device: Mapping[str, Any]) -> str:
    """The unique_id prefix of the heat pump a DeviceInfo describes.

    The device's identifier is (domain, device key), set up in one place, so
    an entity, the page and the registry tidy-up all read the key off it
    rather than each building it again from the entry.
    """
    return unique_prefix(next(iter(device["identifiers"]))[1])


def unique_id(device: Mapping[str, Any], key: str) -> str:
    """An entity's unique_id: the heat pump's prefix and the entity's own key."""
    return f"{prefix_of(device)}{key}"


def mac_hex(text: Any) -> str | None:
    """A MAC address as its twelve hex digits, or None when it is not one.

    The display prints its MAC on the system information page, Home
    Assistant's DHCP watcher hands one over without separators, and
    format_mac gives colons: all three come to the same digits here.
    """
    if not isinstance(text, str):
        return None
    digits = _NOT_HEX.sub("", text.lower())
    return digits if len(digits) == 12 else None


def mac_address(text: Any) -> str | None:
    """A MAC address the way the device registry writes it, "02:00:00:00:84:89"."""
    digits = mac_hex(text)
    if digits is None:
        return None
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


@dataclass(frozen=True)
class Known:
    """What the DHCP flow needs of one configured entry."""

    entry_id: str
    host: str | None
    #: The MAC the display gave on its system information page, if read.
    mac: str | None


#: The DHCP flow's verdicts: a configured unit at a new address, a configured
#: unit where it already is, and a unit nobody has configured.
MOVED = "moved"
KNOWN = "known"
NEW = "new"


def match_discovery(
    known: Iterable[Known], ip: str, mac: str | None
) -> tuple[str, str | None]:
    """What a DHCP discovery of ``ip`` with ``mac`` is, and which entry it is about.

    The MAC decides first, since it follows the unit and the address does
    not: a configured unit whose MAC turns up at another address has moved.
    Only an entry set up with the kind of address a lease hands out can have
    moved, though. One set up with a host name, "CTC8489" or a name of the
    router's DNS, already follows the unit wherever its lease goes, and one
    set up with an IPv6 address is not reached through the lease at all:
    either is the unit where it already is, and is left on what somebody
    chose rather than moved to the lease's address and reloaded there (see
    leased_address). An entry whose MAC has never been read can only be
    recognised by its address. Anything else is a unit nobody has configured
    yet.
    """
    entries = list(known)
    wanted = mac_hex(mac)
    if wanted is not None:
        for item in entries:
            if mac_hex(item.mac) == wanted:
                if item.host == ip or not leased_address(item.host, ip):
                    return KNOWN, item.entry_id
                return MOVED, item.entry_id
    for item in entries:
        if item.host == ip:
            return KNOWN, item.entry_id
    return NEW, None


def leased_address(host: Any, ip: str) -> bool:
    """Whether an entry's ``host`` is the kind of address the lease of ``ip`` hands out.

    An IP address of the same version as the one DHCP saw, and so one a new
    lease takes away. A host name is not: DNS follows the unit, and moving the
    entry off it would close the session on the name and open one on the
    address in the same breath, two clients of the one pump, which is what the
    controller answers with a reset. Neither is an address of the other
    version, which the lease does not hand out.
    """
    try:
        return ipaddress.ip_address(host).version == ipaddress.ip_address(ip).version
    except (TypeError, ValueError):
        return False


def free_key(host: str, taken: Iterable[str]) -> str:
    """The key a new entry at ``host`` is known by: its address, unless that is taken.

    An entry the DHCP flow has moved keeps the key of the address it was
    created with, so a second heat pump that later takes that address cannot
    be known by it too: its device would be the first one's, and so would
    every unique_id. It is given the address with a number after it,
    "192.0.2.55#2", which no address and no host name can be, and with which
    neither key's unique_ids begin with the other's prefix.
    """
    used = set(taken)
    if host not in used:
        return host
    number = 2
    while f"{host}#{number}" in used:
        number += 1
    return f"{host}#{number}"


def moved_data(data: Mapping[str, Any], host: str) -> dict[str, Any]:
    """An entry's data with its address moved, and its device key pinned first.

    The key is written before the address changes, so an entry from before
    the key existed goes on being known by the address it was created with
    rather than taking up the new one.
    """
    return {**data, CONF_DEVICE_KEY: device_key(data), CONF_HOST: host}


def moved_title(title: str, old: str, new: str) -> str:
    """The entry's title with the address in it moved, where it is the one set-up wrote.

    Set-up titles an entry "<model> (<address>)". A title somebody has
    written themselves is theirs, and is left as it is.
    """
    suffix = f" ({old})"
    if old and title.endswith(suffix):
        return f"{title[: -len(suffix)]} ({new})"
    return title


# ------------------------------------------------------------ display rows


def row_key(page: int, screen: int, first_var: int) -> str:
    """A display row's key: the page, the screen and the first variable it reads.

    Where the row stands rather than what it is called, so a name read better
    renames the entity and keeps it. Two readings of one row, "Brine in/ut",
    read different variables and so have different keys; a clock row read as
    hours and minutes is one reading, keyed by its first variable.
    """
    return f"p{page}_s{screen}_v{first_var}"


def union_by_page(*menus: Iterable[SlowPage]) -> list[SlowPage]:
    """Every page of these menus once, the first menu's copy of a page winning.

    The stored menu and the stored selection are written together from the
    same reading, but an installation from before the whole menu was kept has
    only the selection, so both are asked.
    """
    pages: list[SlowPage] = []
    known: set[int] = set()
    for menu in menus:
        for page in menu:
            if page.page not in known:
                known.add(page.page)
                pages.append(page)
    return pages


def _place(value: SlowValue, candidates: list[SlowValue], taken: set[int]) -> SlowValue | None:
    """The row of a page's new reading that stands where ``value`` stood.

    The same screen and the same variables first, each new row taken once,
    in order, so two copies of one place pair in the order they are drawn.
    Then a row on the same screen that reads the old row's first variable:
    the i255 once drew its clock row as two integers, "Drift /24 h:m 1" and
    "2", which read 27 and 26, and the one row the parser makes of them now
    reads both. Then a row on the same screen that shares any variable.
    """
    variables = list(value.var_indices)
    here = [(index, row) for index, row in enumerate(candidates) if row.screen == value.screen]
    for index, row in here:
        if index not in taken and list(row.var_indices) == variables:
            taken.add(index)
            return row
    if not variables:
        return None
    for _index, row in here:
        if variables[0] in row.var_indices:
            return row
    for _index, row in here:
        if set(variables) & set(row.var_indices):
            return row
    return None


def migrate_keys(old: Iterable[SlowPage], new: Iterable[SlowPage]) -> dict[str, str]:
    """For each row of the old menu, the key of the row at its place in the new one.

    Paired on the page, the screen and the variables, never on the name: the
    name is what changed. A row whose place the new menu does not have, a
    page that was not read this time or a row the display no longer draws, is
    left out, and keeps whatever it had. Two old rows may pair with one new
    one, as the clock row's two halves do; see with_previous_keys for which
    of them the new row carries on from.
    """
    by_page: dict[int, list[SlowValue]] = {}
    for page in new:
        by_page.setdefault(page.page, []).extend(page.values)
    pairs: dict[str, str] = {}
    for page in old:
        candidates = by_page.get(page.page, [])
        taken: set[int] = set()
        for value in page.values:
            row = _place(value, candidates, taken)
            if row is not None:
                pairs.setdefault(value.key, row.key)
    return pairs


def _parent(row: SlowValue, parents: list[SlowValue]) -> SlowValue | None:
    """Of the old rows that pair with ``row``, the one it carries on from.

    The one at exactly its place, else the one that read its first variable,
    else the first: the clock row carries on from its hours, and the minutes'
    entity is left for the registry tidy-up, as the parser that folded the
    two halves left it.
    """
    for parent in parents:
        if list(parent.var_indices) == list(row.var_indices):
            return parent
    for parent in parents:
        if parent.var_indices and row.var_indices and parent.var_indices[0] == row.var_indices[0]:
            return parent
    return parents[0] if parents else None


def with_previous_keys(old: Iterable[SlowPage], new: Iterable[SlowPage]) -> list[SlowPage]:
    """The new menu, each row carrying the key it had in the old one where that differs.

    A row whose key is the same carries on whatever it carried, so a reading
    that comes before the set-up that applies the move does not lose it.
    What set-up does with it is previous_keys.
    """
    old_pages = list(old)
    new_pages = list(new)
    by_key = {value.key: value for page in old_pages for value in page.values}
    parents: dict[str, list[SlowValue]] = {}
    for old_key, new_key in migrate_keys(old_pages, new_pages).items():
        parents.setdefault(new_key, []).append(by_key[old_key])
    result: list[SlowPage] = []
    for page in new_pages:
        values: list[SlowValue] = []
        for value in page.values:
            parent = _parent(value, parents.get(value.key, []))
            if parent is None:
                previous = None
            elif parent.key == value.key:
                previous = parent.previous_key
            else:
                previous = parent.key
            values.append(replace(value, previous_key=previous))
        result.append(replace(page, values=values))
    return result


def previous_keys(menu: Iterable[SlowPage]) -> dict[str, str]:
    """Old key to new key, for every row of a menu that has moved.

    What set-up carries over: the entity's unique_id in the registry, and the
    row's place in the seen and display stores. A previous key that is some
    row's key now is never taken for an old one.
    """
    rows = [value for page in menu for value in page.values]
    current = {value.key for value in rows}
    return {
        value.previous_key: value.key
        for value in rows
        if value.previous_key
        and value.previous_key != value.key
        and value.previous_key not in current
    }
