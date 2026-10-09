"""The skip tally reaches the device page under a real Home Assistant core (F1.1, F8.1).

test_homeassistant_skips.py holds the skip rule on the coordinator; this holds
the seam to the diagnostic sensor Senaste displayskörd, which read the tally
under a name the coordinator never had and so always showed 0 beside the
reason. The panel is moved by hand after the first harvest, each skipped
round must show as one more in "hoppade över i rad", and the round that goes
ahead anyway sets it back to 0.

Shares the stand-ins and fixtures of test_homeassistant.py and runs the same
way, from a virtual environment that has Home Assistant and
pytest-homeassistant-custom-component installed:

    python -m pytest \\
        -o asyncio_mode=auto tests/test_homeassistant_skip_tally.py
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from test_homeassistant import (  # noqa: E402,F401  (the fixtures travel by import)
    _advance,
    _entity_id,
    _needs_auto_asyncio_mode,
    stubs,
)
from test_homeassistant_skips import (  # noqa: E402
    HISTORY,
    HOME,
    INTERVAL,
    OTHER,
    MovablePanel,
    _set_up_with_one_page,
)

from homeassistant.config_entries import ConfigEntryState  # noqa: E402

from custom_components.ctc_ecozenith.const import DOMAIN, HARVEST_SKIP_LIMIT  # noqa: E402
from custom_components.ctc_ecozenith.harvest import MIN_FIRST_DELAY  # noqa: E402


async def test_each_skipped_round_shows_on_the_diagnostic_sensor(hass, stubs):
    MovablePanel.instances.clear()
    with patch(f"custom_components.{DOMAIN}.CtcWebClient", MovablePanel):
        entry = await _set_up_with_one_page(hass)
        assert entry.state is ConfigEntryState.LOADED
        (panel,) = MovablePanel.instances
        sensor = _entity_id(hass, "sensor", "display_harvest")

        await _advance(hass, MIN_FIRST_DELAY + 1)
        assert panel.moves == [HISTORY, HOME]
        state = hass.states.get(sensor)
        assert state.attributes["hoppade över i rad"] == 0
        assert state.attributes["senaste skäl"] is None

        # Somebody opens another page: every round given way is one more on
        # the device page, beside the reason, so the owner can see how close
        # to the limit the harvest is.
        panel.page = OTHER
        for skipped in range(1, HARVEST_SKIP_LIMIT + 1):
            await _advance(hass, INTERVAL + 1)
            state = hass.states.get(sensor)
            assert state.attributes["hoppade över i rad"] == skipped
            assert state.attributes["senaste skäl"] == "panelen används av någon annan"

        # The round that goes ahead anyway starts the count over.
        await _advance(hass, INTERVAL + 1)
        assert panel.moves == [HISTORY, HOME, HISTORY, OTHER]
        state = hass.states.get(sensor)
        assert state.attributes["hoppade över i rad"] == 0
        assert state.attributes["senaste skäl"] is None
