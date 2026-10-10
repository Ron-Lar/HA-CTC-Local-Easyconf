"""Which networks the search sweeps: Home Assistant's own adapters, and nothing else (L8).

The search used to fall back to the operating system when the adapters gave
nothing: a blocking name lookup on the event loop that, inside the container,
named the docker bridge's /24, a sweep of nothing useful. And an adapter on a
/16 was passed over as too large, which is exactly when that fallback took over.
Now a large network is swept as the /24 around Home Assistant's own address,
and no adapter means no sweep at all, so the flow goes to the address form.

Addresses here are private ranges no house uses, except where a test is about
an address on the internet; nothing here is ever asked of the network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket

import pytest

from conftest import COMPONENT, ROOT


def _adapter(*addresses: tuple[str, int], enabled: bool = True) -> dict:
    return {
        "name": "eth0",
        "enabled": enabled,
        "ipv4": [{"address": ip, "network_prefix": prefix} for ip, prefix in addresses],
        "ipv6": [],
    }


def _net(text: str) -> ipaddress.IPv4Network:
    return ipaddress.IPv4Network(text)


def test_a_network_too_large_to_sweep_is_swept_as_the_24_around_home_assistant(discovery):
    networks = discovery.networks_from_adapters([_adapter(("192.168.7.20", 16))])
    assert networks == [_net("192.168.7.0/24")]


def test_a_network_within_the_limit_is_swept_whole(discovery):
    assert discovery.networks_from_adapters([_adapter(("192.168.6.10", 23))]) == [
        _net("192.168.6.0/23")
    ]
    assert discovery.networks_from_adapters([_adapter(("172.16.4.9", 22))]) == [
        _net("172.16.4.0/22")
    ]
    assert discovery.MAX_SWEEP == 1024


def test_what_is_no_network_for_a_display_is_left_out(discovery):
    adapters = [
        _adapter(("127.0.0.1", 8)),
        _adapter(("169.254.10.20", 16)),
        _adapter(("192.168.9.5", 24), enabled=False),
        {"name": "broken", "enabled": True, "ipv4": [{"address": "not an address", "network_prefix": 24}]},
        {"name": "no prefix", "enabled": True, "ipv4": [{"address": "192.168.3.3"}]},
        {"name": "no ipv4", "enabled": True},
    ]
    assert discovery.networks_from_adapters(adapters) == []


def test_two_adapters_on_one_network_sweep_it_once(discovery):
    adapters = [_adapter(("192.168.1.4", 24)), _adapter(("192.168.1.9", 24), ("192.168.2.9", 24))]
    assert discovery.networks_from_adapters(adapters) == [
        _net("192.168.1.0/24"),
        _net("192.168.2.0/24"),
    ]


def test_no_adapters_is_an_empty_list_without_asking_the_operating_system(discovery, monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the operating system was asked for an address")

    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    monkeypatch.setattr(socket, "gethostname", refuse)

    async def none(hass):
        return []

    async def broken(hass):
        raise RuntimeError("network integration not loaded")

    monkeypatch.setattr(discovery, "_async_adapters", none)
    assert asyncio.run(discovery.async_home_assistant_networks(object())) == []
    monkeypatch.setattr(discovery, "_async_adapters", broken)
    assert asyncio.run(discovery.async_home_assistant_networks(object())) == []
    assert not hasattr(discovery, "local_networks")


def test_the_adapters_are_what_decides(discovery, monkeypatch):
    async def adapters(hass):
        return [_adapter(("192.168.7.20", 16))]

    monkeypatch.setattr(discovery, "_async_adapters", adapters)
    assert asyncio.run(discovery.async_home_assistant_networks(object())) == [
        _net("192.168.7.0/24")
    ]


def test_an_empty_list_sweeps_nothing(discovery):
    # No session is needed, since nothing is asked of the network.
    assert asyncio.run(discovery.async_discover(None, [])) == []


def test_the_module_no_longer_looks_up_its_own_address():
    from conftest import COMPONENT

    source = (COMPONENT / "discovery.py").read_text(encoding="utf-8")
    assert "getaddrinfo" not in source
    assert "import socket" not in source


@pytest.mark.parametrize("prefix", [8, 12, 16, 21])
def test_any_network_beyond_the_limit_becomes_one_24(discovery, prefix):
    (network,) = discovery.networks_from_adapters([_adapter(("172.20.30.40", prefix))])
    assert network == _net("172.20.30.0/24")


# ------------------------------------------------ never a stranger's network


def test_an_address_on_the_internet_is_never_swept(discovery):
    # Home Assistant on a rented server, whose eth0 has a public /20, or
    # straight on a fibre line that hands out a public /22: a sweep there is
    # a port scan of a provider's other customers. The /22 used to be swept
    # whole and the /20 as the /24 around Home Assistant.
    adapters = [_adapter(("164.90.140.12", 20)), _adapter(("85.229.10.12", 22))]
    assert discovery.networks_from_adapters(adapters) == []


def test_a_server_sweeps_its_private_network_and_not_its_public_one(discovery):
    adapters = [_adapter(("164.90.140.12", 20), ("172.16.4.9", 24))]
    assert discovery.networks_from_adapters(adapters) == [_net("172.16.4.0/24")]


def test_the_shared_range_of_carrier_grade_nat_and_tailscale_is_swept(discovery):
    # 100.64.0.0/10 is neither private nor global; a house behind an
    # operator's NAT, or reached over Tailscale, is still a house.
    assert discovery.networks_from_adapters([_adapter(("100.101.20.30", 10))]) == [
        _net("100.101.20.0/24")
    ]


@pytest.mark.parametrize(
    ("name", "words"),
    [
        ("strings.json", ("private networks", "public addresses is never searched")),
        ("translations/en.json", ("private networks", "public addresses is never searched")),
        ("translations/sv.json", ("privata nät", "publika adresser genomsöks aldrig")),
    ],
)
def test_the_first_step_says_only_private_networks_are_searched(name, words):
    texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
    first = texts["config"]["step"]["user"]["description"].split("\n\n")[0]
    for word in words:
        assert word in first, f"{word!r} saknas i {name}"


def test_the_readme_says_only_private_networks_are_searched():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    setup = " ".join(readme.split("## Setup")[1].split("\n## ")[0].split())
    assert "only the private ones" in setup
    assert "public addresses" in setup and "never searched" in setup
