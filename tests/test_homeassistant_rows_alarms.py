"""Display rows and alarms under a real Home Assistant core (L3, R30).

Two things the ordinary suite cannot see end to end. A display row that reads
CTC's "not fitted" marker at set-up gets no entity until it first leaves a
number, and then gets it through async_add_entities with no reload; and a
registry entry for a row that is no longer on a page the menu still has is
removed, while one for a page the menu does not know is left alone. And the
alarm the panel prints in its header icon becomes the sensor Senaste larm and
an episode on the binary Larm, with the outdoor temperature Modbus had.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest -o asyncio_mode=auto tests/test_homeassistant_rows_alarms.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    HOST,
    FakePanel,
    _entity_id,
    _needs_auto_asyncio_mode,
    _set_up,
    stubs,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402

from custom_components.ctc_ecozenith.catalogue import pages_to_storage  # noqa: E402
from custom_components.ctc_ecozenith.const import (  # noqa: E402
    CONF_MENU,
    CONF_SLOW_PAGES,
    DOMAIN,
    SlowPage,
    SlowValue,
)

PAGE = 22
SCREEN = 220
E017 = "[E017] Givare solpaneler ut"


def _row(key: str, label: str, index: int) -> SlowValue:
    return SlowValue(
        key=f"p{PAGE}_{key}", label=label, page=PAGE, screen=SCREEN,
        fmt="%.1f", var_indices=[index], unit="°C", scale=0.1,
    )


#: One heat pump page as the sweep records it: two rows that read, two that
#: read CTC's marker on an air to water unit, as on VSH's i255.
HEATPUMP = SlowPage(
    page=PAGE, title="Värmepump", screens=[1, 0, SCREEN],
    values=[
        _row("utetemperatur", "Utetemperatur", 0),
        _row("vp_in_ut_1", "VP in/ut 1", 1),
        _row("brine_in_ut_1", "Brine in/ut 1", 2),
        _row("brine_in_ut_2", "Brine in/ut 2", 3),
    ],
)


class ServingPanel(FakePanel):
    """A display standing on the heat pump page, answering its values; never moved."""

    vars: list[int] = []
    header: list[str] = []
    chrome: list[int] = [0]

    async def async_current_page(self) -> int:
        return PAGE

    async def async_screen_map(self, refresh: bool = False) -> dict[int, list[int]]:
        return {PAGE: [1, 0, SCREEN]}

    async def async_vars(self, screen) -> list[int]:
        return list(ServingPanel.vars) if screen == SCREEN else list(ServingPanel.chrome)

    async def async_goto_page(self, target: int, route=None) -> bool:
        return target == PAGE

    async def async_step_back_to(self, target: int, hops: int = 6) -> bool:
        return target == PAGE

    async def async_goto_home(self, hops: int = 6) -> int | None:
        return PAGE

    async def async_alarm_candidates(self, screen: int, values) -> list[str]:
        return list(ServingPanel.header)


async def _set_up_with_the_page(hass):
    stored = pages_to_storage([HEATPUMP])
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", ServingPanel):
        entry = await _set_up(hass, **{CONF_SLOW_PAGES: stored, CONF_MENU: stored})
    assert entry.state is ConfigEntryState.LOADED
    return entry


async def _harvest_again(hass, entry) -> None:
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", ServingPanel):
        await entry.runtime_data.web.async_refresh()
        await hass.async_block_till_done()


def _has_entity(hass, key: str) -> bool:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_p{PAGE}_{key}") is not None


# --------------------------------------------------------------- the alarm


async def test_the_alarm_the_panel_prints_becomes_a_sensor_and_an_episode(hass, stubs):
    """From the header icon to Senaste larm and the binary's episodes, and back.

    The alarm is read off the harvest, so it costs the panel nothing, and it
    is concluded once per round with the outdoor temperature Modbus had then
    (7.2 °C in the stand-in). A sensor alarm leaves the heat pump running, so
    the binary itself, which reads the Modbus status, stays off while the log
    holds the open episode; when a round shows no alarm the episode is closed
    and the sensor keeps the alarm with "pågår: nej".
    """
    ServingPanel.vars = [72, 215, 9999, 9999]
    ServingPanel.header = [E017]
    entry = await _set_up_with_the_page(hass)

    last = hass.states.get(_entity_id(hass, "sensor", "last_alarm"))
    assert last.state == E017
    assert last.attributes["kod"] == "E017"
    assert last.attributes["text"] == "Givare solpaneler ut"
    assert last.attributes["pågår"] == "ja" and last.attributes["slut"] is None
    assert last.attributes["utetemperatur vid start"] == 7.2
    assert last.attributes["start"]

    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert alarm.state == "off", "status 0 i Modbus: pumpen går, larmet är en givare"
    (episode,) = alarm.attributes["episoder"]
    assert episode["kod"] == "E017" and episode["slut"] is None and episode["utetemperatur"] == 7.2

    # The next round shows no alarm: the episode is closed, the sensor keeps it.
    ServingPanel.header = []
    await _harvest_again(hass, entry)
    last = hass.states.get(_entity_id(hass, "sensor", "last_alarm"))
    assert last.state == E017
    assert last.attributes["pågår"] == "nej" and last.attributes["slut"]
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert alarm.attributes["episoder"][0]["slut"]

    # And the same alarm again is a new episode, newest first.
    ServingPanel.header = [E017]
    await _harvest_again(hass, entry)
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert [e["slut"] is None for e in alarm.attributes["episoder"]] == [True, False]


async def test_without_a_display_page_there_is_no_alarm_sensor(hass, stubs):
    entry = await _set_up(hass)
    assert entry.state is ConfigEntryState.LOADED
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_{HOST}_last_alarm") is None
    alarm = hass.states.get(_entity_id(hass, "binary_sensor", "alarm"))
    assert "episoder" not in alarm.attributes
