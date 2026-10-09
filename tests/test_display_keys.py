"""A display row's key is its identity, and a better name must not move it (F5.1, F9.1).

sensor.py builds unique_id from the key, so every key that changes leaves an
entity behind in the registry, unavailable, with a new twin beside it. The keys
in tests/fixtures/keys_0_15_1.json are the ones the parser of 0.15.1 built from
the four real pages, which is what the houses and every external installation
carry. The one exception is spelled out below: the i255 drew the clock row as
two integers with keys _1 and _2, and those two are one reading now, under the
key the i550 Pro has always had for the same row.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from conftest import FIXTURES, load

#: The page each fixture is harvested as.
PAGES = {"i255_118": 22, "i255_128": 30, "i550_136": 30, "i550_138": 22}

#: Keys of 0.15.1 that are gone on purpose, and the one key their reading has now.
FOLDED = {"i255_128": ({"p30_drift_24_h_m_1", "p30_drift_24_h_m_2"}, "p30_drift_24_h_m")}


class FakeClient:
    """Hands async_page_values the widgets of one real screen."""

    def __init__(self, data):
        self._data = data

    async def async_widgets(self, screen):
        assert screen == self._data["screen"]
        widget = load("web_api").Widget
        return [widget(**w) for w in self._data["widgets"]]


def _keys(catalogue, page, name):
    data = page(name)
    values = asyncio.run(
        catalogue.async_page_values(FakeClient(data), PAGES[name], [data["screen"]])
    )
    return [value.key for value in values]


@pytest.fixture(scope="module")
def keys_0_15_1():
    return json.loads((FIXTURES / "keys_0_15_1.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_key_of_0_15_1_is_still_built_in_the_same_order(catalogue, page, keys_0_15_1, name):
    gone, instead = FOLDED.get(name, (set(), None))
    expected = [key for key in keys_0_15_1[name] if key not in gone]
    now = _keys(catalogue, page, name)
    assert [key for key in now if key != instead] == expected
    assert set(now) - set(expected) == ({instead} if instead else set())


def test_the_fixture_of_keys_covers_the_four_pages_in_full(keys_0_15_1):
    assert set(keys_0_15_1) == set(PAGES)
    assert {name: len(keys) for name, keys in keys_0_15_1.items()} == {
        "i255_118": 37, "i255_128": 21, "i550_136": 16, "i550_138": 35,
    }


def test_the_clock_row_keeps_the_key_the_i550_has_always_had(catalogue, page):
    for name in ("i255_128", "i550_136"):
        keys = _keys(catalogue, page, name)
        assert "p30_drift_24_h_m" in keys, name
        assert not any(key.startswith("p30_drift_24_h_m_") for key in keys), name
        assert "p30_drift_24" not in keys, name


def test_the_key_comes_from_the_rows_own_name_and_the_name_is_free_to_improve(catalogue):
    # The h:m says how the panel prints the row. It leaves the name, not the key.
    assert catalogue._key_label("Drift /24 h:m") == "Drift /24 h:m"
    assert catalogue._clean_label("Drift /24 h:m") == "Drift /24"
    assert catalogue._slug(catalogue._key_label("Drift /24 h:m"), "x") == "drift_24_h_m"
    # Everything else a key was ever built from is built the same way as the name.
    for label, expected in (
        ("Antal starter /24 h", "Antal starter /24"),
        ("Total drifttid h", "Total drifttid"),
        ("Drifttid total", "Drifttid total"),
        ("Avgiven värme (kW)", "Avgiven värme"),
        ("Energi el/30 dagar (kWh)", "Energi el/30 dagar"),
        ("Medeltemperatur ute/30 dagar °C", "Medeltemperatur ute/30 dagar"),
        ("Brine in/ut °C 2", "Brine in/ut 2"),
        ("Värde 8", "Värde 8"),
    ):
        assert catalogue._key_label(label) == expected, label
        assert catalogue._clean_label(label) == expected, label


def test_a_clock_row_stored_before_the_minutes_reads_its_hours_until_the_menu_is_read_again(
    catalogue, const
):
    # An i550 Pro's row as 0.15.1 stored it in the options: the clock format,
    # both variables, and no unit. The panel read 03:46.
    stored = catalogue.pages_from_storage([{
        "page": 30, "title": "Historisk driftinfo", "screens": [136],
        "values": [{
            "key": "p30_drift_24_h_m", "label": "Drift /24 h:m", "screen": 136,
            "fmt": "%02d:%02d", "vars": [7, 8], "unit": None, "scale": 1.0,
        }],
    }])[0].values[0]
    raw = [0] * 7 + [3, 46]
    assert stored.unit is None
    assert catalogue.numeric_value(stored, raw) == 3.0
    # Once the menu has been read again the same key carries the unit, and the
    # figure follows it in the same step.
    fresh = const.SlowValue(
        key="p30_drift_24_h_m", label="Drift /24", page=30, screen=136,
        fmt="%02d:%02d", var_indices=[7, 8], unit="min",
    )
    assert catalogue.numeric_value(fresh, raw) == 226.0
