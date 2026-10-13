"""The page's source line names a display row's screen and place, under a real
Home Assistant core (R39).

dashboard.py collects each running pump for the page and now hands every
display value's screen and its place in the page's list to the source line,
so rows that share a name on one page can be told apart. Here VSH's seven
pages are set up against a display that answers, harvested once, and the page
is built the way the frontend asks for it.

Shares the stand-ins and fixtures of test_homeassistant.py and the answering
display of test_homeassistant_harvest.py, and runs the same way, from a
virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_list_source.py
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: F401  (the fixtures travel by import)
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

from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY


async def test_the_collection_gives_every_display_value_its_screen_and_place(
    hass, hass_storage, stubs
):
    from custom_components.ctc_ecozenith import dashboard

    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    (pump,) = dashboard._collect(hass)
    heating = next(page for page in pump["pages"] if page["title"] == "Värmesystem")
    (value,) = heating["values"]
    # VSH's test pages have one reading each, on the page's one screen.
    assert (value["label"], value["screen"], value["row"]) == ("Utetemperatur", 210, 1)
    assert all(
        v["row"] == place
        for page in pump["pages"]
        for place, v in enumerate(page["values"], start=1)
    )


async def test_the_list_of_all_values_says_where_a_display_row_is(hass, hass_storage, stubs):
    from custom_components.ctc_ecozenith import dashboard

    # The page speaks Home Assistant's language (R23); this one is Swedish.
    hass.config.language = "sv"
    entry = await _vsh_entry(hass)
    await _set_up(hass, entry)
    # A display row is made when it first reads as a number (L3).
    await _fire(hass, MIN_FIRST_DELAY + 1)
    entity_id = _entity_id(hass, "sensor", "p21_utetemperatur")
    assert entity_id

    config = dashboard.build_config(hass)
    (values,) = [view for view in config["views"] if view["path"] == "values"]
    card = values["sections"][0]["cards"][1]
    (row,) = [row for row in card["rows"] if row.get("entity") == entity_id]
    assert row["source"] == "Displaysidan Värmesystem, skärm 210, rad 1, hämtas var 30:e minut"
    assert card["show_empty"] == "Visa även tomma"
