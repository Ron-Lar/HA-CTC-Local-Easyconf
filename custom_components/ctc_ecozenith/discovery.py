"""Find CTC displays on the local network.

The display has no mDNS, no reverse DNS and a locally administered MAC in the
02:00:00:00 range, so there is nothing to look up. What it does have is a
distinctive fingerprint: port 80 answers 400 to almost everything, but
``/settings/name`` returns the settings file name, which is ``settings_ezi2xx.bin``
on an EcoZenith i255 and ``settings_ezi5xx.bin`` on an i550 Pro.

Scanning is therefore a two stage sweep: open the TCP port on every address in
the candidate networks, then ask the ones that answer for that file name. The
candidate networks are the private networks of Home Assistant's own adapters
and nothing else; see async_home_assistant_networks and networks_from_adapters.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

import aiohttp

_LOGGER = logging.getLogger(__name__)

PORT_CONCURRENCY = 60
PROBE_CONCURRENCY = 8
PORT_TIMEOUT = 0.4
PROBE_TIMEOUT = 4.0

#: The most addresses one adapter's network is swept for. A larger network, a
#: /16 at home or in an office, is swept as the /24 around Home Assistant's own
#: address on it rather than passed over (roadmap L8).
MAX_SWEEP = 1024

# The settings file names a family, not an exact model. The name chosen here is
# the member of each family that has the display with Modbus TCP, since that is
# the only kind this integration can talk to at all. A family joins the list
# once the fleet has shown its stem (roadmap R19); stats_extra.MODEL_SLUGS must
# hold every name given here, and tests/test_model_names.py says so.
MODEL_NAMES = {
    "ezi2xx": "EcoZenith i255",
    "ezi3xx": "EcoZenith i360",
    "ezi5xx": "EcoZenith i550 Pro",
    "ecologic": "EcoLogic",
}

#: The model of a heat pump whose display has not said which one it is: set up
#: on Modbus alone (roadmap R11), or named by a settings file nobody has seen
#: yet, as "EcoZenith (<stem>)" (roadmap R19). The device is then called
#: "CTC EcoZenith", where the bare manufacturer would have made it "CTC CTC".
FAMILY = "EcoZenith"

#: What a stem may look like to be written into a name. The name becomes the
#: device's and the prefix of every entity id, so a display that answered with
#: something stranger is named after the family alone.
STEM_PATTERN = re.compile(r"^[A-Za-z0-9_]{1,24}$")


def settings_stem(settings_name: str) -> str:
    """The family part of the display's settings file: "ezi2xx" of settings_ezi2xx.bin."""
    return settings_name.removeprefix("settings_").removesuffix(".bin")


def model_name(stem: str) -> str:
    """The model a settings file names: a known family, else the family with the stem.

    An unknown stem used to give "CTC (<stem>)", which made the device "CTC
    CTC (<stem>)". "EcoZenith (<stem>)" is the right sort of name for every
    CTC controller with this display, and still shows which stem turned up.
    """
    if stem in MODEL_NAMES:
        return MODEL_NAMES[stem]
    if STEM_PATTERN.match(stem):
        return f"{FAMILY} ({stem})"
    return FAMILY


@dataclass
class DiscoveredDisplay:
    """A CTC display found on the network."""

    host: str
    settings_name: str

    @property
    def stem(self) -> str:
        """The family part of the settings file, kept with a new entry."""
        return settings_stem(self.settings_name)

    @property
    def model(self) -> str:
        return model_name(self.stem)

    @property
    def label(self) -> str:
        """What the list of found units and a discovered flow's card show.

        The model first and the address in brackets, the way the entry title
        reads, so the card of a discovered unit names the heat pump rather than
        an address, and without the dash the list used to carry.
        """
        return f"{self.model} ({self.host})"


async def _port_open(host: str, port: int, timeout: float = PORT_TIMEOUT) -> bool:
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (asyncio.TimeoutError, OSError):
        return False
    writer.close()
    try:
        await writer.wait_closed()
    except (OSError, asyncio.TimeoutError):  # pragma: no cover
        pass
    return True


@dataclass
class WebProbe:
    """What one host said on its web port when asked for the settings file."""

    #: The display, when the answer was a CTC settings file name.
    display: DiscoveredDisplay | None
    #: Whether anything answered on the port at all. A host that answers with
    #: something else is not a CTC display; one that does not answer may still
    #: be a CTC whose web interface is switched off (roadmap R11).
    answered: bool


async def async_probe_web(
    session: aiohttp.ClientSession, host: str, port: int = 80
) -> WebProbe:
    """Ask one host whether it is a CTC display, and tell silence from a no.

    Silence is a refused or unreachable port, or no answer in time. Anything
    else that comes back, a status other than 200, a body that is not a
    settings file name, a connection dropped halfway through the answer, is
    something answering that is not a CTC display.
    """
    url = f"http://{host}:{port}/settings/name"
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=PROBE_TIMEOUT)
        ) as response:
            if response.status != 200:
                return WebProbe(None, answered=True)
            body = (await response.text()).strip()
    except (aiohttp.ClientConnectorError, asyncio.TimeoutError):
        return WebProbe(None, answered=False)
    except (aiohttp.ClientError, UnicodeDecodeError):
        return WebProbe(None, answered=True)
    if body.startswith("settings_") and body.endswith(".bin"):
        return WebProbe(DiscoveredDisplay(host=host, settings_name=body), answered=True)
    return WebProbe(None, answered=True)


async def async_probe_host(
    session: aiohttp.ClientSession, host: str, port: int = 80
) -> DiscoveredDisplay | None:
    """Ask one host whether it is a CTC display."""
    return (await async_probe_web(session, host, port)).display


def networks_from_adapters(
    adapters: Iterable[Mapping[str, Any]],
) -> list[ipaddress.IPv4Network]:
    """The networks to sweep, out of Home Assistant's own list of adapters.

    Each enabled adapter's IPv4 network is swept whole, with its own prefix, so
    a /23 is covered as much as a /24. A network larger than MAX_SWEEP used to
    be passed over, which on a /16 left nothing at all to sweep; it is now swept
    as the /24 around Home Assistant's own address on it, where a heat pump on
    a home network is all but certain to be. Loopback and link local addresses
    are no network a display sits on and are left out.

    So is an address on the internet. Home Assistant on a rented server, or
    straight on a fibre line that hands out public addresses, would otherwise
    sweep up to a thousand strangers' addresses on port 80, which a provider
    reads as a port scan; such an installation types the display's address
    instead. Private networks are swept, and so is the shared range that
    carrier-grade NAT and Tailscale use (100.64.0.0/10), which is not global
    either.
    """
    networks: list[ipaddress.IPv4Network] = []
    for adapter in adapters:
        if not adapter.get("enabled", True):
            continue
        for address in adapter.get("ipv4") or []:
            ip = address.get("address")
            prefix = address.get("network_prefix")
            if not ip or prefix is None:
                continue
            try:
                own = ipaddress.IPv4Address(ip)
                candidate = ipaddress.IPv4Network(f"{ip}/{prefix}", strict=False)
            except ValueError:
                continue
            if own.is_loopback or own.is_link_local or own.is_global:
                continue
            if candidate.num_addresses > MAX_SWEEP:
                candidate = ipaddress.IPv4Network(f"{ip}/24", strict=False)
            if candidate not in networks:
                networks.append(candidate)
    return networks


async def _async_adapters(hass) -> list[Mapping[str, Any]]:
    """Home Assistant's adapters, imported here so the module loads without it."""
    from homeassistant.components import network as ha_network

    return await ha_network.async_get_adapters(hass)


async def async_home_assistant_networks(hass) -> list[ipaddress.IPv4Network]:
    """Return the networks Home Assistant itself is attached to, and only those.

    Home Assistant usually runs in a container, so asking the operating system
    for "my" address returns the container bridge rather than the network the
    heat pump is on. Home Assistant knows the real adapters, including their
    prefix. There is no fallback to the operating system any more: it gave the
    container bridge's /24, a sweep of nothing useful, and it did so with a
    blocking name lookup on the event loop. Without an adapter the list is
    empty, and the set-up flow goes straight to the address form (roadmap L8).
    """
    try:
        adapters = await _async_adapters(hass)
    except Exception as err:  # noqa: BLE001 - no adapters is an empty sweep, not a failure
        _LOGGER.debug("Could not read adapters from Home Assistant: %s", err)
        return []
    return networks_from_adapters(adapters)


async def async_discover(
    session: aiohttp.ClientSession,
    networks: list[ipaddress.IPv4Network],
    port: int = 80,
) -> list[DiscoveredDisplay]:
    """Sweep the given networks and return every CTC display found."""
    if not networks:
        return []

    hosts = [str(ip) for network in networks for ip in network.hosts()]
    open_hosts: list[str] = []
    port_gate = asyncio.Semaphore(PORT_CONCURRENCY)

    async def check(host: str) -> None:
        async with port_gate:
            if await _port_open(host, port):
                open_hosts.append(host)

    await asyncio.gather(*(check(host) for host in hosts))
    _LOGGER.debug("%s hosts answer on port %s", len(open_hosts), port)

    found: list[DiscoveredDisplay] = []
    probe_gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def probe(host: str) -> None:
        async with probe_gate:
            display = await async_probe_host(session, host, port)
            if display is not None:
                found.append(display)

    await asyncio.gather(*(probe(host) for host in open_hosts))
    found.sort(key=lambda d: tuple(int(part) for part in d.host.split(".")))
    return found
