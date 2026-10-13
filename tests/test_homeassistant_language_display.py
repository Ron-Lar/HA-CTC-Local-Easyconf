"""Every entity of a heat pump with a display is named in Home Assistant's language,
the display rows under the panel's own names, under a real core (R23).

VSH's seven pages are set up against a display that answers and harvested once,
under an English Home Assistant. Every entity the integration names itself,
the harvest's own account and the last alarm among them, takes its English
name from en.json; every display row keeps the
name the panel prints, page title and row, in the Swedish the menu is read in,
and the page explains it in English all the same.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
display of test_homeassistant_harvest.py, and runs the same way, from a
virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_language_display.py
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.helpers import entity_registry as er

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
    HOST,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs_fixture,
)
from test_homeassistant_harvest import (  # noqa: F401  (live_panel is autouse)
    _fire,
    _set_up,
    _vsh_entry,
    live_panel,
)

from custom_components.ctc_ecozenith.const import DOMAIN
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / DOMAIN
EN = json.loads((COMPONENT / "translations" / "en.json").read_text(encoding="utf-8"))["entity"]
DISPLAY_ROW = re.compile(r"p\d+_")


async def test_every_entity_is_named_in_english_and_every_display_row_by_the_panel(
    hass, hass_storage, stubs
):
    hass.config.language = "en"
    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    # A display row is made when it first reads as a number (L3).
    await _fire(hass, MIN_FIRST_DELAY + 1)
    prefix = f"{DOMAIN}_{HOST}_"
    items = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    rows = [item for item in items if DISPLAY_ROW.match(item.unique_id[len(prefix):])]
    own = [item for item in items if item not in rows]
    assert rows, "the harvest made display rows"
    # The two entities only a display brings: its harvest's account and the
    # alarm it last showed.
    assert {"display_harvest", "last_alarm"} <= {item.unique_id[len(prefix):] for item in own}
    for item in own:
        key = item.unique_id[len(prefix):]
        assert item.original_name == EN[item.domain][key]["name"], item.entity_id
    heating = next(item for item in rows if item.unique_id.endswith("_p21_utetemperatur"))
    assert heating.original_name == "Värmesystem: Utetemperatur"


async def test_an_english_page_explains_a_display_row_in_english_under_its_panel_name(
    hass, hass_storage, stubs
):
    from custom_components.ctc_ecozenith import dashboard

    hass.config.language = "en"
    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    await _fire(hass, MIN_FIRST_DELAY + 1)
    entity_id = _entity_id(hass, "sensor", "p21_utetemperatur")
    config = json.loads(json.dumps(dashboard.build_config(hass)))
    (values,) = [view for view in config["views"] if view["path"] == "values"]
    card = values["sections"][0]["cards"][1]
    (row,) = [row for row in card["rows"] if row.get("entity") == entity_id]
    assert row["name"] == "Utetemperatur"
    assert row["explanation"] == (
        "The outdoor temperature, as this part of the installation measures it."
    )
    assert row["source"] == "Display page Värmesystem, screen 210, row 1, read every 30 minutes"
    assert card["show_empty"] == "Show empty ones too"
    # The page's group for the display page is the panel's own title.
    assert {"heading": "Värmesystem"} in card["rows"]
