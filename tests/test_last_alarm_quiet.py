"""Senaste larm stays readable when the display goes quiet (F5.3, R30).

The sensor's state and attributes come out of the alarm log's store, not out
of the harvest's data, so a display that stops answering changes nothing
about the latest alarm. The sensor nevertheless followed the web
coordinator's last_update_success, against its own comment: after three
missed harvests it went unavailable, lost its attributes in the history and
showed an automation a change of state that no alarm made. Built against a
stand-in runtime here; the same under a real core, with three failed
harvests, is in test_homeassistant_alarm_quiet.py.
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


E017 = {
    "code": "E017",
    "text": "Givare solpaneler ut",
    "shown": "[E017] Givare solpaneler ut",
    "start": "2026-10-09T06:00:00+00:00",
    "end": None,
    "outdoor": 7.2,
}


def _runtime(latest, success: bool) -> SimpleNamespace:
    return SimpleNamespace(
        web=SimpleNamespace(data={}, last_update_success=success),
        alarms=SimpleNamespace(latest=latest),
        device={"identifiers": {("ctc_ecozenith", "pump")}},
    )


def test_the_latest_alarm_is_available_while_the_display_is_quiet(sensor):
    entity = sensor.CtcLastAlarmSensor(_runtime(E017, success=False))
    assert entity.available is True, "larmet kommer ur loggen, inte ur skörden"
    assert entity.native_value == "[E017] Givare solpaneler ut"
    attributes = entity.extra_state_attributes
    assert attributes["kod"] == "E017"
    assert attributes["pågår"] == "ja"
    assert attributes["utetemperatur vid start"] == 7.2


def test_the_sensor_is_available_while_empty_too(sensor):
    entity = sensor.CtcLastAlarmSensor(_runtime(None, success=False))
    assert entity.available is True
    assert entity.native_value is None
    assert entity.extra_state_attributes is None


def test_the_comment_and_the_code_agree():
    # The comment promised a sensor readable after the display had gone
    # quiet while the code returned last_update_success; both now say True.
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "custom_components" / "ctc_ecozenith" / "sensor.py"
    ).read_text(encoding="utf-8")
    body = source.split("class CtcLastAlarmSensor")[1].split("\nclass ")[0]
    available = body.split("def available")[1]
    assert "return True" in available
    assert "last_update_success" not in available
