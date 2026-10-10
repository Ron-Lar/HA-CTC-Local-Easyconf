"""A row the display hides at the moment the menu is read stays on its page.

On VSH's heat pump page (i255, page 22, screen 118) one slot shows the
compressor's speed, variable 37, while the compressor runs, and a status,
variable 38, while it stands still. The menu read at 0.19.0's upgrade came
while it stood still, so the speed's row was not in it, and the registry
tidy-up took the row's entity away (2026-10-10). Keys are places now, so a
row seen once is a place on its page: it is kept, and pairs with itself.
"""

from __future__ import annotations

from conftest import load

keys = load("keys")
const = load("const")
rows = load("rows")

SlowPage, SlowValue = const.SlowPage, const.SlowValue


def _value(key: str, var: int, label: str, screen: int = 118) -> SlowValue:
    return SlowValue(key=key, label=label, page=22, screen=screen, fmt="%.1f", var_indices=[var])


def _page(*values: SlowValue) -> SlowPage:
    return SlowPage(page=22, title="Driftinfo Värmepump", screens=[118], route=[], values=list(values))


RUNNING = _page(
    _value("p22_s118_v36", 36, "Utetemperatur"),
    _value("p22_s118_v37", 37, "Kompressor"),
)
STANDING = _page(
    _value("p22_s118_v36", 36, "Utetemperatur"),
    _value("p22_s118_v38", 38, "Status"),
)


def test_a_row_hidden_at_the_reading_stays_under_its_place():
    (page,) = keys.keep_hidden_rows([RUNNING], [STANDING])
    assert [value.key for value in page.values] == ["p22_s118_v36", "p22_s118_v38", "p22_s118_v37"]
    kept = page.values[-1]
    assert kept.label == "Kompressor" and kept.var_indices == [37] and kept.screen == 118


def test_both_turns_of_the_slot_survive_any_order_of_readings():
    menu = [RUNNING]
    for reading in ([STANDING], [RUNNING], [STANDING]):
        menu = keys.keep_hidden_rows(menu, reading)
    assert sorted(value.key for value in menu[0].values) == [
        "p22_s118_v36", "p22_s118_v37", "p22_s118_v38",
    ]


def test_an_old_name_key_that_is_hidden_moves_to_its_place_and_keeps_its_entity():
    # VSH's case at the upgrade: the stored menu still had the name keys.
    before = [_page(_value("p22_utetemperatur", 36, "Utetemperatur"), _value("p22_kompressor", 37, "Kompressor"))]
    menu = keys.with_previous_keys(before, keys.keep_hidden_rows(before, [STANDING]))
    moved = keys.previous_keys(menu)
    assert moved == {"p22_utetemperatur": "p22_s118_v36", "p22_kompressor": "p22_s118_v37"}
    # And the tidy-up finds nothing to take away on the page.
    registry = ["p22_s118_v36", "p22_s118_v37"]
    assert rows.vanished_display_keys(menu, registry) == set()


def test_a_page_the_reading_did_not_reach_is_left_to_the_caller():
    other = SlowPage(page=30, title="Historik", screens=[128], route=[], values=[
        SlowValue(key="p30_s128_v22", label="Avgiven värme totalt", page=30, screen=128, fmt="%d", var_indices=[22]),
    ])
    assert keys.keep_hidden_rows([RUNNING, other], [STANDING]) == keys.keep_hidden_rows([RUNNING], [STANDING])


def test_both_readers_keep_hidden_rows():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components" / "ctc_ecozenith"
    assert "keep_hidden_rows(before, menu)" in (root / "__init__.py").read_text(encoding="utf-8")
    assert "keep_hidden_rows(before, pages)" in (root / "config_flow.py").read_text(encoding="utf-8")
