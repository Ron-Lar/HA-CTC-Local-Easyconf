"""Every number an explanation names is the code's number (R22).

The texts behind the ⓘ drifted from the code twice: two registers went on
saying "Avstängd som standard" after 0.14.0 created every reading switched
on, and the lifetime floor said fifty after it had become ten (the floors
are held in test_cop_explanations.py). Here the rest is held, without Home
Assistant: no text calls an entity switched off that is created switched on,
and every age, interval, count and register number the texts give is read
against the constant or the register table it stands for.
"""

from __future__ import annotations

import re

import pytest

from conftest import COMPONENT

#: The numbers the texts write out as words.
WORDS = {"ett": 1, "en": 1, "två": 2, "tre": 3, "fem": 5, "tio": 10}

SWITCHED_OFF = re.compile(r"avstängd(a)? som standard|off by default", re.IGNORECASE)


def _every_text(explanations) -> dict[str, str]:
    texts: dict[str, str] = {}
    for table in ("MODBUS", "CONTROL", "DERIVED", "IDENTITY", "HARVEST"):
        texts.update({f"{table}:{key}": text for key, text in getattr(explanations, table).items()})
    texts.update({f"DISPLAY:{prefix}": text for prefix, text in explanations._DISPLAY})
    return texts


def _pair(pattern: str, text: str) -> tuple[int, int]:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    return int(found.group(1)), int(found.group(2))


def _one(pattern: str, text: str) -> int:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    value = found.group(1)
    return int(value) if value.isdigit() else WORDS[value]


# ------------------------------------------------------- switched on or off


def test_no_text_calls_a_reading_switched_off_that_is_created_switched_on(const, explanations):
    enabled = {d.key: d.enabled_default for d in const.MODBUS_SENSORS + const.MODBUS_SETTINGS}
    for key, text in explanations.MODBUS.items():
        says_off = bool(SWITCHED_OFF.search(text))
        assert says_off == (not enabled.get(key, True)), (key, text)


def test_nothing_else_is_said_to_be_switched_off_either(explanations):
    # The controls, the derived values, the identity rows and the display rows
    # are all created switched on, so none of them may say otherwise.
    for name, text in _every_text(explanations).items():
        if name.startswith("MODBUS:"):
            continue
        assert not SWITCHED_OFF.search(text), name


def test_the_register_table_keeps_no_stale_comment_about_a_default(const):
    from conftest import COMPONENT

    source = (COMPONENT / "const.py").read_text(encoding="utf-8")
    table = source.split("MODBUS_SENSORS: Final")[1].split("MODBUS_SETTINGS: Final")[0]
    assert "Off by default" not in table


# ------------------------------------------- the coefficient of performance


def test_the_ages_of_the_samples_are_the_trackers(cop, const, explanations):
    assert _pair(r"(\d+) till (\d+) timmar", explanations.explain("cop_day")) == (
        cop.DAY_MIN_HOURS,
        cop.DAY_MAX_HOURS,
    )
    for span in ("week", "month"):
        days, tolerance = cop.WINDOWS[span]
        text = explanations.explain(f"cop_{span}")
        assert _one(r"senaste (\d+) dygnen", text) == days, span
        assert _pair(r"avläsning (\d+) till (\d+) dygn", text) == (days, days + tolerance), span
    year = explanations.explain("cop_year")
    assert _pair(r"(\d+) till (\d+) dagar", year) == (const.COP_WINDOW_DAYS, cop.YEAR_MAX_DAYS)


def test_the_yearly_floor_is_named_and_is_the_trackers(cop, explanations):
    # The yearly figure divides like the lifetime one, above the same floor.
    assert _one(r"minst (\d+) kWh", explanations.explain("cop_year")) == cop.MIN_CONSUMPTION_KWH


# ------------------------------------------------------ intervals and counts


def test_the_intervals_are_the_defaults(const, explanations):
    assert _one(r"var (\d+):e sekund", explanations.explain("starts_today")) == const.DEFAULT_FAST_INTERVAL
    harvest = explanations.explain("display_harvest")
    assert _one(r"var (\d+):e minut", harvest) * 60 == const.DEFAULT_SLOW_INTERVAL
    assert _one(r"försöker om efter (\w+) minuter", harvest) * 60 == const.RETRY_INTERVAL
    assert _one(r"högst (\w+) varv i rad", harvest) == const.HARVEST_SKIP_LIMIT


def test_the_volatile_controls_are_written_and_forgotten_as_the_manager_does(const, explanations):
    text = explanations.explain("ctl_room_setpoint_1")
    assert "varje minut" in text and const.CONTROL_KEEPALIVE_SECONDS == 60
    assert _one(r"på (\w+) minuter släpps", text) * 60 == const.CONTROL_EXPIRY_SECONDS
    assert _one(r"glömmer cirka (\w+) minuter", text) * 60 == const.CONTROL_EXPIRY_SECONDS
    assert _one(r"inom cirka (\w+) minuter", explanations.explain("release_control")) * 60 == (
        const.CONTROL_EXPIRY_SECONDS
    )


def test_the_alarm_log_keeps_as_many_episodes_as_the_text_says(const, explanations):
    assert _one(r"de (\w+) senaste larm", explanations.explain("alarm")) == const.ALARM_EPISODES


def test_the_days_counts_say_a_reload_clears_them_while_nothing_keeps_them(explanations):
    """Starts and defrosts today begin again with every set-up (F4.4).

    The watch that counts them is built anew in async_setup_entry and keeps
    nothing on disk, so saved options, the menu written after an update and
    Home Assistant's reload after entities were switched on clear them just as
    a restart does. The texts said midnight and a restart only. Should the
    watch ever be kept across a reload, this fails and the texts change with it.
    """
    transitions = (COMPONENT / "transitions.py").read_text(encoding="utf-8")
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    setup = init.split("async def async_setup_entry(")[1].split("\nasync def ")[0]
    assert "TransitionWatch()" in setup, "byggs på nytt vid varje uppsättning"
    sensors = (COMPONENT / "sensor.py").read_text(encoding="utf-8")
    for kept in ("Store(", "RestoreEntity", "RestoreSensor", "async_get_last"):
        assert kept not in transitions, kept
        assert kept not in sensors, kept
    for key in ("starts_today", "defrosts_today"):
        text = explanations.explain(key)
        assert "Nollas vid midnatt och när posten laddas om" in text, key
        assert "omstart av Home Assistant" in text and "alternativen sparas" in text, key
        assert "räknas sedan" in text, key


# --------------------------------------------------------------- registers


def _addresses(const) -> dict[str, int]:
    rows = const.MODBUS_SENSORS + const.MODBUS_SETTINGS + const.CONTROL_NUMBERS + const.CONTROL_SELECTS
    return {row.key: row.address for row in rows}


def test_every_register_a_text_names_is_one_the_integration_reads(const, explanations):
    known = set(_addresses(const).values())
    for name, text in _every_text(explanations).items():
        for number in re.findall(r"\b(6[12]\d{3})\b", text):
            assert int(number) in known, (name, number)


@pytest.mark.parametrize(
    ("key", "register_key"),
    [
        ("dhw_temp_raw", "dhw_temp"),
        ("set_hp1_blocked", "set_hp1_blocked"),
        ("mean_run_24h", "compressor_hours_24h"),
    ],
)
def test_a_register_named_in_a_text_is_the_one_meant(const, explanations, key, register_key):
    address = _addresses(const)[register_key]
    assert str(address) in explanations.explain(key), (key, address)


def test_every_source_names_the_registers_own_address(const, explanations):
    for key, address in _addresses(const).items():
        assert f"Modbus-register {address}" in explanations.source(key), key
    known = set(_addresses(const).values())
    for key in (
        "compressor_running", "last_start", "mean_run_24h", "events",
        "immersion_active", "smartgrid_active",
    ):
        for number in re.findall(r"\b(6\d{4})\b", explanations.source(key)):
            assert int(number) in known, (key, number)
