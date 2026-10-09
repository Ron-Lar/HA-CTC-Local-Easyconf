"""The device's own account of the harvest, and honest empty states (R34).

The web coordinator kept the moment of every reading and the reason for a
skipped cycle, and nothing showed them: a value twenty minutes old looked like
one two days old. The page's source line read the interval off the
coordinator, which is five minutes while the display is being retried, and a
pump in setup_retry looked like no pump at all. Now a diagnostic sensor per
entry carries the last harvest as a timestamp with the tallies as attributes,
the page reads the configured interval, and the empty page names the pump
being retried and the reason Home Assistant gives. The entity under a real
core is in test_homeassistant_harvest.py.
"""

from __future__ import annotations

import pathlib

from test_dashboard import NEW_HA

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"


def _source(name: str) -> str:
    return (COMPONENT / name).read_text(encoding="utf-8")


def _note(config) -> str:
    (view,) = config["views"]
    (section,) = view["sections"]
    _heading, note = section["cards"]
    assert note["type"] == "markdown"
    return note["content"]


# ---------------------------------------------------------- the empty page


def test_a_pump_being_retried_is_named_with_the_reason(dashboard_views):
    waiting = [{"name": "EcoZenith i255 (192.0.2.55)", "reason": "no Modbus register could be read"}]
    note = _note(dashboard_views.build_dashboard([], "sv", NEW_HA, waiting=waiting))
    assert note.startswith("EcoZenith i255 (192.0.2.55) svarar inte just nu (no Modbus register could be read).")
    assert "försöker igen" in note
    english = _note(dashboard_views.build_dashboard([], "en", NEW_HA, waiting=waiting))
    assert "is not answering right now (no Modbus register could be read)" in english
    assert "retries by itself" in english


def test_a_retry_without_a_reason_has_no_empty_brackets(dashboard_views):
    note = _note(dashboard_views.build_dashboard([], "sv", NEW_HA, waiting=[{"name": "CTC", "reason": None}]))
    assert note.startswith("CTC svarar inte just nu. ")
    assert "()" not in note


def test_several_pumps_being_retried_get_a_line_each(dashboard_views):
    waiting = [{"name": "A", "reason": "x"}, {"name": "B", "reason": "y"}]
    note = _note(dashboard_views.build_dashboard([], "sv", NEW_HA, waiting=waiting))
    assert note.count("svarar inte just nu") == 2


def test_with_nobody_waiting_the_page_says_nothing_is_running(dashboard_views):
    note = _note(dashboard_views.build_dashboard([], "sv", NEW_HA, waiting=[]))
    assert note.startswith("Ingen CTC-värmepump är igång")
    assert _note(dashboard_views.build_dashboard([], "sv", NEW_HA)) == note


def test_the_page_asks_for_the_retried_pumps_and_announces_the_failed_set_up():
    dashboard = _source("dashboard.py")
    waiting = dashboard.split("def _waiting")[1].split("\ndef ")[0]
    assert "ConfigEntryState.SETUP_RETRY" in waiting
    assert "entry.reason" in waiting
    assert "waiting=_waiting(hass)" in dashboard
    setup = _source("__init__.py").split("async def async_setup_entry")[1].split("\nasync def ")[0]
    failure = setup.split("await modbus.async_config_entry_first_refresh()")[1].split("web_client = ")[0]
    assert "dashboard.async_announce_change(hass)" in failure, "sidan sägs till även på felvägen"


# ------------------------------------------------------- the source line


def test_the_source_line_reads_the_configured_interval():
    collect = _source("dashboard.py").split("def _collect")[1].split("\ndef ")[0]
    assert "CONF_SLOW_INTERVAL" in collect
    assert "update_interval" not in collect, "koordinatorns eget intervall är 300 s under fel"


# --------------------------------------------------------- the sensor


def test_the_harvest_sensor_is_a_diagnostic_timestamp_that_stays_available():
    body = _source("sensor.py").split("class CtcHarvestSensor")[1].split("\nclass ")[0]
    assert "SensorDeviceClass.TIMESTAMP" in body
    assert "EntityCategory.DIAGNOSTIC" in body
    assert 'unique_id(runtime.device, "display_harvest")' in body
    available = body.split("def available")[1].split("def ")[0]
    assert "return True" in available, "ett misslyckat varv är just det den ska visa"
    assert "last_harvest" in body.split("def native_value")[1].split("def ")[0]


def test_the_harvest_sensor_reads_its_tallies_with_defaults():
    # The skip tally is another track's (R8); the sensor must work before and
    # after it lands, so every tally is read with getattr and a default.
    attributes = _source("sensor.py").split("class CtcHarvestSensor")[1].split("def extra_state_attributes")[1]
    for name in ('"hoppade över i rad"', '"misslyckade i rad"', '"nästa försök"', '"senaste skäl"',
                 '"sidor lästa"', '"sidor missade"'):
        assert name in attributes, name
    for read in (
        'getattr(coordinator, "skips_in_a_row", 0)',
        'getattr(coordinator, "last_skip_reason", None)',
        'coordinator, "last_failure", None',
        'getattr(patience, "failures", 0)',
        'getattr(coordinator, "next_attempt", None)',
    ):
        assert read in attributes, read


def test_the_sensor_is_created_beside_the_display_sensors():
    setup = _source("sensor.py").split("async def async_setup_entry")[1].split("\nclass ")[0]
    assert "CtcHarvestSensor(runtime)" in setup
    assert setup.index("if runtime.web is not None") < setup.index("CtcHarvestSensor(runtime)")


# ------------------------------------------------------- on the page


def test_the_harvest_sensor_has_a_place_and_an_explanation(dashboard_views, explanations):
    assert "display_harvest" in dashboard_views._KNOWN_KEYS
    unit = next(keys for _id, _icon, keys in dashboard_views._TECHNICAL if _id == "unit")
    assert "display_harvest" in unit
    assert explanations.explain("display_harvest").startswith("När displayens sidor senast lästes")
    assert explanations.source("display_harvest") == "Integrationens egen bokföring av displayskörden"
    for text in explanations.HARVEST.values():
        assert "—" not in text and " – " not in text and " - " not in text


def test_the_new_words_use_no_dashes_as_punctuation(dashboard_views):
    for lang in ("sv", "en"):
        text = dashboard_views.TEXT[lang]["setup_retry"]
        assert "—" not in text and " – " not in text and " - " not in text
        assert "{name}" in text and "{reason}" in text
