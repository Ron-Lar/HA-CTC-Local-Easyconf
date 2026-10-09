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
so the device and every entity stay what they were.

Free of Home Assistant, like rows.py and seen.py: the rules are tested on their
own, and the platforms, the page and the registry tidy-up all ask here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .const import CONF_DEVICE_KEY, DOMAIN

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
    An entry whose MAC has never been read can only be recognised by its
    address. Anything else is a unit nobody has configured yet.
    """
    entries = list(known)
    wanted = mac_hex(mac)
    if wanted is not None:
        for item in entries:
            if mac_hex(item.mac) == wanted:
                return (KNOWN if item.host == ip else MOVED), item.entry_id
    for item in entries:
        if item.host == ip:
            return KNOWN, item.entry_id
    return NEW, None


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
