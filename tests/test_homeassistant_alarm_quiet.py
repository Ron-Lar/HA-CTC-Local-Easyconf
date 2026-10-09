"""Senaste larm through a quiet display, under a real Home Assistant core (F5.3, R30).

An alarm is logged off the harvest, then the display's web server stops
answering: three harvests in a row fail and the web coordinator reports the
failure, so the display rows go unavailable and Senaste displayskörd counts
three misses. Senaste larm keeps its state and its attributes through all of
it, since the alarm log is a store of its own and the alarm neither began nor
ended.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_alarm_quiet.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)
from test_homeassistant_rows_alarms import (  # noqa: E402
    E017,
    HEATPUMP,
    ServingPanel,
    _row_entity,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MENU,
    CONF_SLOW_PAGES,
    DOMAIN,
    HARVEST_PATIENCE,
)
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402
from custom_components.ctc_ecozenith.web_api import CtcWebError  # noqa: E402


class QuietingPanel(ServingPanel):
    """The serving panel, whose web server can be told to stop answering."""

    quiet = False

    async def async_current_page(self) -> int:
        if QuietingPanel.quiet:
            raise CtcWebError("/vars/menu timed out")
        return await super().async_current_page()


async def test_the_latest_alarm_outlives_a_display_that_stops_answering(hass, stubs):
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = [E017]
    QuietingPanel.quiet = False
    stored = pages_to_storage([HEATPUMP])
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", QuietingPanel):
        entry = await _set_up(hass, **{CONF_SLOW_PAGES: stored, CONF_MENU: stored})
    assert entry.state is ConfigEntryState.LOADED
    await _advance(hass, MIN_FIRST_DELAY + 1)  # the first harvest is planned, not run in set-up (R6)

    last_alarm = _entity_id(hass, "sensor", "last_alarm")
    before = hass.states.get(last_alarm)
    assert before.state == E017 and before.attributes["pågår"] == "ja"
    row = _row_entity(hass, "utetemperatur")
    assert hass.states.get(row).state == "7.2"

    # The display goes quiet: as many harvests as Patience allows fail in a
    # row, and the coordinator reports the failure.
    QuietingPanel.quiet = True
    web = entry.runtime_data.web
    for _ in range(HARVEST_PATIENCE):
        await web.async_refresh()
        await hass.async_block_till_done()
    assert web.last_update_success is False
    assert hass.states.get(row).state == "unavailable", "displayens egna rader släcks"
    harvest = hass.states.get(_entity_id(hass, "sensor", "display_harvest"))
    assert harvest.attributes["misslyckade i rad"] == HARVEST_PATIENCE

    # Senaste larm neither began nor ended: same state, same attributes.
    after = hass.states.get(last_alarm)
    assert after.state == E017, "larmet släcks inte för att displayen tiger"
    assert after.attributes["kod"] == "E017"
    assert after.attributes["pågår"] == "ja"
    assert after.attributes["start"] == before.attributes["start"]
    assert after.attributes["utetemperatur vid start"] == 7.2
