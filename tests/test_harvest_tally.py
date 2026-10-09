"""The diagnostic sensor reads the skip tally under the coordinator's own name (F1.1, F8.1).

The sensor used to ask the coordinator for ``skipped_in_a_row`` while the
coordinator counts ``skips_in_a_row``, and the default of 0 hid the slip: the
device page said "senaste skäl: panelen används av någon annan" beside
"hoppade över i rad: 0", so nobody could see how close to HARVEST_SKIP_LIMIT
the harvest was. A source test even pinned the wrong name. These build the
sensor against a stand-in coordinator and read the attribute back, so a
default can never again stand in for a name that does not exist. The same
under a real core, with the panel moved by hand, is in
test_homeassistant_skip_tally.py.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import ha_stub
from conftest import load


@pytest.fixture(scope="module")
def sensor():
    ha_stub.skip_unless_stubbed()
    return load("sensor")


def _runtime(web) -> SimpleNamespace:
    return SimpleNamespace(web=web, device={"identifiers": {("ctc_ecozenith", "pump")}})


def _coordinator(**fields) -> SimpleNamespace:
    """A web coordinator as the sensor sees it, with the tallies given."""
    base = dict(
        data={},
        last_update_success=True,
        last_harvest=None,
        patience=SimpleNamespace(failures=0),
        next_attempt=None,
        last_skip_reason=None,
        last_failure=None,
        pages_read=[],
        pages_missed=[],
    )
    base.update(fields)
    return SimpleNamespace(**base)


def test_the_skips_in_a_row_are_the_coordinators_own_count(sensor):
    web = _coordinator(skips_in_a_row=2, last_skip_reason="panelen används av någon annan")
    attributes = sensor.CtcHarvestSensor(_runtime(web)).extra_state_attributes
    assert attributes["hoppade över i rad"] == 2, "räknaren heter skips_in_a_row på koordinatorn"
    assert attributes["senaste skäl"] == "panelen används av någon annan"


def test_the_tally_follows_the_coordinator_round_by_round(sensor):
    web = _coordinator(skips_in_a_row=0)
    entity = sensor.CtcHarvestSensor(_runtime(web))
    assert entity.extra_state_attributes["hoppade över i rad"] == 0
    web.skips_in_a_row = 1
    assert entity.extra_state_attributes["hoppade över i rad"] == 1
    web.skips_in_a_row = 0
    assert entity.extra_state_attributes["hoppade över i rad"] == 0


def test_a_coordinator_without_the_tally_still_reads_as_zero(sensor):
    # The default stays: the sensor must not break on a coordinator from
    # before the tally existed, only never hide one that does.
    attributes = sensor.CtcHarvestSensor(_runtime(_coordinator())).extra_state_attributes
    assert attributes["hoppade över i rad"] == 0
    assert attributes["misslyckade i rad"] == 0
