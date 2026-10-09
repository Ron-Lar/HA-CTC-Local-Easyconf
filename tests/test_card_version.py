"""The card is served under the manifest's version, so a release reaches the browser.

The frontend loads the card from an address that carries the integration's
version, and a Lovelace resource of ours at another version is moved to the
new one. The version is the manifest's, read through the integration at
set-up: raising it in the release commit is what makes a browser that has
the old card fetch the new one, and nothing else has to be touched. Held
here in the source, which the ordinary suite can read without Home Assistant.
"""

from __future__ import annotations

import json
import pathlib
import re

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def test_the_manifest_version_is_a_release_number():
    manifest = json.loads(_source("manifest.json"))
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]), manifest["version"]


def test_the_card_address_carries_the_version_the_set_up_reads_from_the_manifest():
    setup = _source("__init__.py").split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "integration = await async_get_integration(hass, DOMAIN)" in setup
    assert "await dashboard.async_register(hass, str(integration.version))" in setup
    register = _source("dashboard.py").split("async def async_register(")[1].split("\nasync def ")[0]
    assert "await async_register_card(hass, version)" in register
    card = _source("card.py")
    assert 'versioned = f"{CARD_URL}?v={version}"' in card
    assert 'frontend.add_extra_js_url(hass, f"{ICON_URL}?v={version}")' in card


def test_a_resource_of_ours_at_another_version_is_moved_to_this_one():
    resource = _source("card.py").split("async def _async_register_resource")[1]
    assert 'if first.get("url") != versioned:' in resource
    assert "await resources.async_update_item(first[\"id\"], {\"res_type\": \"module\", \"url\": versioned})" in resource
    # And never two of ours.
    assert "for item in stale:" in resource and "async_delete_item(" in resource
