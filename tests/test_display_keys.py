"""A display row's key is where it stands, and every key an installation has is carried over (L2).

sensor.py builds unique_id from the key. Up to 0.18.0 the key was built from the
row's name, so every time the parser read a name better the row got a new key
and its entity was left behind in the registry with a twin beside it. Fas 1
froze the name keys (F5.1) and pinned them against tests/fixtures/keys_0_15_1.json,
the keys the parser of 0.15.1 built from the four real pages, which is what the
houses and every external installation carry. Now the key is the row's place,
"p<page>_s<screen>_v<first variable>", and the name is free to improve; the
fixture pins the migration instead: every key of 0.15.1 pairs with exactly one
row at its place in the new reading, for all four pages, and no new row is
left without a parent. The one many to one is spelled out below: the i255 drew
the clock row as two integers, _1 and _2, and both stand where the one row the
parser makes of them now stands. The row carries on from the hours, and the
minutes' entry is left for the registry tidy-up, as 0.17.0 already left it.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from conftest import FIXTURES, load

keys = load("keys")
rows = load("rows")
harvest = load("harvest")
seen_module = load("seen")

#: The page each fixture is harvested as.
PAGES = {"i255_118": 22, "i255_128": 30, "i550_136": 30, "i550_138": 22}

#: The clock row's two halves on the i255, and the one key their row has now.
HALVES = ("p30_drift_24_h_m_1", "p30_drift_24_h_m_2")
CLOCK = "p30_s128_v27"

POSITIONAL = re.compile(r"^p\d+_s\d+_v\d+(_\d+)?$")


class FakeClient:
    """Hands async_page_values the widgets of one real screen."""

    def __init__(self, data, blank: int | None = None):
        self._data = data
        self._blank = blank

    async def async_widgets(self, screen):
        assert screen == self._data["screen"]
        widget = load("web_api").Widget
        out = []
        for w in self._data["widgets"]:
            fields = dict(w)
            if self._blank is not None and fields["index"] == self._blank:
                fields["label"] = ""
            out.append(widget(**fields))
        return out


# ------------------------------------------------- the menus of the past

#: How 0.15.1 to 0.18.0 built a key from a row's name, kept here only to
#: rebuild the menus the installations carry; the integration no longer does.
_KEY_UNIT = re.compile(r"[( ](kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)\)?\s*$")
_POSITION_SUFFIX = re.compile(r"\s+\d+$")


def _key_label(label: str) -> str:
    suffix = _POSITION_SUFFIX.search(label.strip())
    base = _POSITION_SUFFIX.sub("", label.strip())
    cleaned = _KEY_UNIT.sub("", base).strip(" ()") or base
    return f"{cleaned}{suffix.group(0)}" if suffix else cleaned


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(
        r"[^a-z0-9]+", "_", text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
    )
    return cleaned.strip("_") or fallback


def _old_page(catalogue, name: str, data: dict, folded: bool):
    """The page as an earlier release stored it in the options: name keys.

    ``folded`` is 0.17.0 and 0.18.0, whose parser made one row of the i255's
    clock; without it, 0.15.1 and before, which kept the two halves.
    """
    const = load("const")
    page, screen = PAGES[name], data["screen"]
    widgets = asyncio.run(FakeClient(data).async_widgets(screen))
    pairing = catalogue._pair_labels(widgets)
    readings = [
        ((pairing.get(w.index) or f"Värde {w.index}").strip().rstrip(":"), w.value_fmt, list(w.value_vars), w.index)
        for w in widgets
        if w.value_fmt is not None and w.value_vars and w.visible and catalogue.has_conversion(w.value_fmt)
    ]
    if folded:
        readings = catalogue._join_clock_rows(readings)
    seen: set[str] = set()
    values = []
    for raw_label, fmt, indices, index in readings:
        base = _slug(_key_label(raw_label), f"s{screen}_w{index}")
        key = f"p{page}_{base}"
        suffix = 2
        while key in seen:
            key = f"p{page}_{base}_{suffix}"
            suffix += 1
        seen.add(key)
        values.append(
            const.SlowValue(key=key, label=raw_label, page=page, screen=screen, fmt=fmt, var_indices=indices)
        )
    return const.SlowPage(page=page, title=name, screens=[screen], values=values)


def _new_page(catalogue, name: str, data: dict, blank: int | None = None):
    const = load("const")
    values = asyncio.run(
        catalogue.async_page_values(FakeClient(data, blank), PAGES[name], [data["screen"]])
    )
    return const.SlowPage(page=PAGES[name], title=name, screens=[data["screen"]], values=values)


@pytest.fixture(scope="module")
def keys_0_15_1():
    return json.loads((FIXTURES / "keys_0_15_1.json").read_text(encoding="utf-8"))


def test_the_fixture_of_keys_covers_the_four_pages_in_full(keys_0_15_1):
    assert set(keys_0_15_1) == set(PAGES)
    assert {name: len(found) for name, found in keys_0_15_1.items()} == {
        "i255_118": 37, "i255_128": 21, "i550_136": 16, "i550_138": 35,
    }


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_menu_of_0_15_1_is_rebuilt_key_for_key(catalogue, page, keys_0_15_1, name):
    # The rebuilt menu is the one the migration is pinned against, so it has
    # to be the one 0.15.1 wrote: the same keys in the same order.
    assert [v.key for v in _old_page(catalogue, name, page(name), folded=False).values] == keys_0_15_1[name]


# ------------------------------------------------------------- the new key


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_row_is_keyed_by_its_place(catalogue, page, name):
    data = page(name)
    new = _new_page(catalogue, name, data)
    for value in new.values:
        assert POSITIONAL.match(value.key), value.key
        assert value.key == keys.row_key(PAGES[name], data["screen"], value.var_indices[0])
    assert len({v.key for v in new.values}) == len(new.values)


def test_a_name_read_differently_keeps_the_key(catalogue, page):
    # The caption of "Avgiven värme totalt" lost for a round: the row is
    # "Värde N" by name, and the key is the one it always has.
    data = page("i255_128")
    lost = next(w["index"] for w in data["widgets"] if (w.get("label") or "").startswith("Avgiven värme totalt"))
    whole = _new_page(catalogue, "i255_128", data)
    gappy = _new_page(catalogue, "i255_128", data, blank=lost)
    assert [v.key for v in gappy.values] == [v.key for v in whole.values]
    assert [v.label for v in gappy.values] != [v.label for v in whole.values]


def test_the_clock_row_is_keyed_by_its_first_variable(catalogue, page):
    new = _new_page(catalogue, "i255_128", page("i255_128"))
    clock = next(v for v in new.values if v.key == CLOCK)
    assert clock.var_indices == [27, 26] and clock.label == "Drift /24"
    new = _new_page(catalogue, "i550_136", page("i550_136"))
    assert next(v for v in new.values if v.key == "p30_s136_v7").label == "Drift /24"


# ------------------------------------------------------ the migration, pinned


@pytest.mark.parametrize("name", sorted(PAGES))
def test_every_key_of_0_15_1_pairs_with_exactly_one_new_key(catalogue, page, keys_0_15_1, name):
    data = page(name)
    old = _old_page(catalogue, name, data, folded=False)
    new = _new_page(catalogue, name, data)
    pairs = keys.pair_keys([old], [new])
    assert sorted(pairs) == sorted(keys_0_15_1[name]), "varje nyckel ur 0.15.1 får en ny"
    new_keys = {v.key for v in new.values}
    assert set(pairs.values()) <= new_keys
    assert set(pairs.values()) == new_keys, "ingen ny nyckel blir utan förälder"
    # Paired on the place, never on the name.
    old_by_key = {v.key: v for v in old.values}
    new_by_key = {v.key: v for v in new.values}
    for old_key, new_key in pairs.items():
        before, after = old_by_key[old_key], new_by_key[new_key]
        assert before.screen == after.screen
        assert before.var_indices[0] in after.var_indices
    # One to one, but for the clock row's two halves.
    many = {k: [o for o, n in pairs.items() if n == k] for k in set(pairs.values())}
    shared = {k: sorted(v) for k, v in many.items() if len(v) > 1}
    assert shared == ({CLOCK: list(HALVES)} if name == "i255_128" else {})


@pytest.mark.parametrize("name", sorted(PAGES))
def test_the_menu_the_houses_carry_pairs_one_to_one(catalogue, page, name):
    # 0.17.0 and 0.18.0 folded the clock row, so what VSH and PT carry pairs
    # place for place, the variables and all.
    data = page(name)
    old = _old_page(catalogue, name, data, folded=True)
    new = _new_page(catalogue, name, data)
    pairs = keys.pair_keys([old], [new])
    assert len(pairs) == len(old.values) == len(new.values)
    assert sorted(pairs.values()) == sorted(v.key for v in new.values)
    old_by_key = {v.key: v for v in old.values}
    new_by_key = {v.key: v for v in new.values}
    for old_key, new_key in pairs.items():
        assert old_by_key[old_key].var_indices == new_by_key[new_key].var_indices


@pytest.mark.parametrize("name", sorted(PAGES))
def test_each_new_row_carries_the_key_it_had(catalogue, page, keys_0_15_1, name):
    data = page(name)
    old = _old_page(catalogue, name, data, folded=False)
    new = keys.with_previous_keys([old], [_new_page(catalogue, name, data)])
    moves = keys.previous_keys(new)
    expected = set(keys_0_15_1[name]) - ({HALVES[1]} if name == "i255_128" else set())
    assert set(moves) == expected
    assert set(moves.values()) == {v.key for p in new for v in p.values}
    if name == "i255_128":
        assert moves[HALVES[0]] == CLOCK, "klockraden fortsätter från timmarna"
        # The minutes' entry is the one the tidy-up takes, as 0.17.0 did.
        registry = set(keys_0_15_1[name])
        moved = {moves.get(key, key) for key in registry}
        assert rows.vanished_display_keys(new, moved, moved=set(moves)) == {HALVES[1]}
    else:
        registry = {moves[key] for key in keys_0_15_1[name]}
        assert rows.vanished_display_keys(new, registry, moved=set(moves)) == set()


def test_a_later_reading_carries_the_previous_key_on(catalogue, page):
    # A second reading before the set-up that applies the move, a form saved
    # twice while the entry waited to be set up again, must not lose it.
    data = page("i255_118")
    old = _old_page(catalogue, "i255_118", data, folded=True)
    first = keys.with_previous_keys([old], [_new_page(catalogue, "i255_118", data)])
    second = keys.with_previous_keys(first, [_new_page(catalogue, "i255_118", data)])
    assert keys.previous_keys(second) == keys.previous_keys(first)
    assert len(keys.previous_keys(second)) == len(old.values)


def test_a_page_the_reading_did_not_have_keeps_what_it_has(catalogue, page):
    const = load("const")
    data = page("i255_118")
    old = _old_page(catalogue, "i255_118", data, folded=True)
    elsewhere = const.SlowPage(page=99, title="Annan", screens=[990], values=[])
    assert keys.pair_keys([old], [elsewhere]) == {}
    assert keys.previous_keys(keys.with_previous_keys([old], [elsewhere])) == {}
    # And the old page, carried on as it was (a fold-in), pairs with itself.
    kept = keys.with_previous_keys([old], [old])
    assert keys.previous_keys(kept) == {}


def test_a_previous_key_that_is_a_row_now_is_never_moved(const):
    page = const.SlowPage(
        page=22, title="x", screens=[1],
        values=[
            const.SlowValue(key="p22_s1_v1", label="a", page=22, screen=1, fmt="%d", var_indices=[1], previous_key="p22_s1_v2"),
            const.SlowValue(key="p22_s1_v2", label="b", page=22, screen=1, fmt="%d", var_indices=[2], previous_key="p22_b"),
            const.SlowValue(key="p22_s1_v3", label="c", page=22, screen=1, fmt="%d", var_indices=[3], previous_key="p22_s1_v3"),
        ],
    )
    assert keys.previous_keys([page]) == {"p22_b": "p22_s1_v2"}


def test_the_stored_menu_or_the_selection_is_the_old_menu(const):
    a = const.SlowPage(page=1, title="menyn", screens=[1])
    b = const.SlowPage(page=1, title="urvalet", screens=[1])
    c = const.SlowPage(page=2, title="bara urvalet", screens=[2])
    assert [p.title for p in keys.union_by_page([a], [b, c])] == ["menyn", "bara urvalet"]


# --------------------------------------------------------------- storage


def test_the_previous_key_survives_storage_and_is_left_out_where_there_is_none(catalogue, const):
    page = const.SlowPage(page=30, title="Historik", screens=[128])
    page.values.append(const.SlowValue(
        key=CLOCK, label="Drift /24", page=30, screen=128, fmt="%02d:%02d",
        var_indices=[27, 26], unit="min", previous_key="p30_drift_24_h_m",
    ))
    page.values.append(const.SlowValue(key="p30_s128_v20", label="Total drifttid", page=30, screen=128, fmt="%d", var_indices=[20]))
    stored = catalogue.pages_to_storage([page])
    assert stored[0]["values"][0]["previous_key"] == "p30_drift_24_h_m"
    assert "previous_key" not in stored[0]["values"][1], "fältet skrivs bara där raden flyttat"
    restored = catalogue.pages_from_storage(stored)
    assert restored[0].values[0].previous_key == "p30_drift_24_h_m"
    assert restored[0].values[1].previous_key is None
    # A menu from before the field reads as before.
    del stored[0]["values"][0]["previous_key"]
    assert catalogue.pages_from_storage(stored)[0].values[0].previous_key is None


# ---------------------------------------------------- the stores follow


class MemoryStore:
    def __init__(self, data=None):
        self.data = data
        self.saved = []

    async def async_load(self):
        return self.data

    async def async_save(self, data):
        self.saved.append(data)
        self.data = data

    def async_delay_save(self, func, delay):
        self.data = func()


def test_the_seen_record_follows_the_rows():
    store = MemoryStore({"keys": ["p30_energi_el_total", "hp1_rps"], "numeric": ["p30_energi_el_total", "p30_emxxx"]})
    record = seen_module.SeenValues(store)
    asyncio.run(record.async_load())
    moves = {"p30_energi_el_total": "p30_s128_v25", "p30_emxxx": "p30_s128_v28"}
    assert record.rename(moves) is True
    assert record.keys == {"p30_s128_v25", "hp1_rps"}
    assert record.numeric == {"p30_s128_v25", "p30_s128_v28"}
    asyncio.run(record.async_save())
    assert store.saved[-1] == {"keys": ["hp1_rps", "p30_s128_v25"], "numeric": ["p30_s128_v25", "p30_s128_v28"]}
    # Done once, nothing moves again.
    assert record.rename(moves) is False


def test_the_stored_harvest_follows_the_rows_and_the_later_reading_wins():
    t0 = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc)
    stored = harvest.StoredHarvest(
        values={"p30_avgiven_varme_totalt": 22499.0, "p30_s128_v25": 9116.0, "p30_energi_el_total": 9000.0},
        read_at={"p30_avgiven_varme_totalt": t0, "p30_s128_v25": t0, "p30_energi_el_total": t0 - timedelta(days=1)},
        harvested_at=t0,
        consumption=12.0,
    )
    moves = {"p30_avgiven_varme_totalt": "p30_s128_v22", "p30_energi_el_total": "p30_s128_v25"}
    renamed = stored.renamed(moves)
    assert renamed.values == {"p30_s128_v22": 22499.0, "p30_s128_v25": 9116.0}
    assert renamed.read_at == {"p30_s128_v22": t0, "p30_s128_v25": t0}
    assert renamed.harvested_at == t0 and renamed.consumption == 12.0
    # Nothing to move is the same harvest.
    assert stored.renamed({}).as_dict() == stored.as_dict()
    # And what it writes reads back.
    store = MemoryStore()
    memory = harvest.HarvestMemory(store)
    assert asyncio.run(memory.async_save(renamed)) is True
    assert harvest.StoredHarvest.from_dict(store.data).values == renamed.values
