"""The display's screen decoder held to a facit (roadmap R61).

The decoder turns /wp/<screen>, /vars/<screen> and /vars/glob into widgets, and
everything the integration reads off the display rests on it: where a reading
stands, which caption names it, which format prints it, which variables it
reads. The suite used to test its pieces one by one and FakePanel's imitation
of the whole, so a change to the rule that the text slot sits at 10 for images
and 12 for texts, or to which element of a t0 triplet points into t1, would have
passed every test and shown up as an installation whose rows lost their names.

These run the real client (CtcWebClient.async_widgets, async_english_label,
async_screen_def, and the gzip handling in _request) over a fake session, no
network. The display's own answers come from tests/fixtures: screen 118,
captured from an i255 driving an EcoAir 720M, gzipped here the way the display
always sends it, and every catalogue text answered with the placeholder
T<id>, so the facit pins which entry each caption is read from without
depending on what the entry says. The facit,
tests/fixtures/golden_wp_118.json, pins every widget's index, kind, geometry,
visibility, text id, label, format, variables and string content.

The facit is checked against something the decoder under test did not make:
widgets_i255_118.json, the same screen captured at another moment with its
captions resolved in Swedish. Every widget agrees but one, whose text the
compressor's state picks, and that one agrees too once the variable that picks
it is set the way it stood then.

An i550 Pro screen is not here. Its definition was never captured raw: the two
i550 fixtures, widgets_i550_136.json and widgets_i550_138.json, hold decoded
widgets, not the definition, and capturing one means asking an i550 Pro's
display for /wp/<screen>, which these tests do not do. What an i550 screen
draws that screen 118 does not (a clock in one format over two variables, a
string element, a caption picked by a global variable, three-slot alternatives
counted by a variable) is reached by a constructed screen at the end of this
file, whose facit is written by hand, not taken from the decoder.

To add a capture: the display's /wp/<screen> (gzipped, so fetch it as bytes and
unpack it), /vars/<screen> and /vars/glob, saved as wp_<screen>.js,
vars_<screen>.txt and vars_glob.txt beside the others. Then run this file once
with CTC_WRITE_FACIT=1, which writes golden_wp_<screen>.json and fails on
purpose, read the facit through against the panel, and commit both. A
definition without a facit fails test_every_captured_definition_has_a_facit.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os

from conftest import FIXTURES
from fake_panel import DisplaySession
import pytest

#: What the facit pins of every widget, in this order.
FIELDS = (
    "index", "kind", "x", "y", "width", "height", "visible",
    "text_id", "label", "value_fmt", "value_vars", "text_value",
)

CAPTURED = sorted(int(path.stem.split("_")[1]) for path in FIXTURES.glob("wp_*.js"))


def run(coro):
    return asyncio.run(coro)


def facit_path(screen: int):
    return FIXTURES / f"golden_wp_{screen}.json"


def as_facit(widgets) -> list[dict]:
    return [{name: getattr(widget, name) for name in FIELDS} for widget in widgets]


def _vars(screen: int, changes: dict[int, int] | None = None) -> bytes:
    fields = (FIXTURES / f"vars_{screen}.txt").read_text(encoding="utf-8").strip().split("|")
    for index, value in (changes or {}).items():
        fields[index] = str(value)
    return "|".join(fields).encode()


def capture_session(screen: int, changes: dict[int, int] | None = None, plain: bool = False):
    """The display as captured: the definition gzipped (or not), the variables
    as they stood (or with ``changes``), and the catalogue answering T<id>."""
    raw = (FIXTURES / f"wp_{screen}.js").read_bytes()
    definition = raw if plain else gzip.compress(raw)

    def answer(method, path, body):
        if path == f"/wp/{screen}":
            return 200, definition
        if path == f"/vars/{screen}":
            return 200, _vars(screen, changes)
        if path == "/vars/glob":
            return 200, (FIXTURES / "vars_glob.txt").read_bytes()
        if path.startswith("/txt/"):
            return 200, f"T{path.split('/')[3]}".encode()
        return 404, b""

    return DisplaySession(answer)


def client_for(web_api, session):
    return web_api.CtcWebClient(session, "192.0.2.10")


def assert_matches(got: list[dict], want: list[dict]) -> None:
    """Equal, and where not, the first widget that differs and how."""
    for mine, theirs in zip(got, want, strict=False):
        if mine != theirs:
            changed = {
                k: (mine.get(k), theirs.get(k)) for k in FIELDS if mine.get(k) != theirs.get(k)
            }
            pytest.fail(f"widget {theirs['index']} differs from the facit (got, facit): {changed}")
    assert len(got) == len(want), f"{len(got)} widgets against the facit's {len(want)}"


# ------------------------------------------------------------ screen 118


@pytest.mark.parametrize("screen", CAPTURED)
def test_a_captured_screen_decodes_to_its_facit(web_api, screen):
    widgets = run(client_for(web_api, capture_session(screen)).async_widgets(screen))
    if os.environ.get("CTC_WRITE_FACIT") == "1":
        rows = ",\n".join("  " + json.dumps(row, ensure_ascii=False) for row in as_facit(widgets))
        facit_path(screen).write_text(
            f'{{\n "screen": {screen},\n "widgets": [\n{rows}\n ]\n}}\n', encoding="utf-8"
        )
        pytest.fail(f"{facit_path(screen).name} written; read it through and commit it")
    facit = json.loads(facit_path(screen).read_text(encoding="utf-8"))
    assert facit["screen"] == screen
    assert_matches(as_facit(widgets), facit["widgets"])


def test_every_captured_definition_has_a_facit():
    assert CAPTURED, "the capture of screen 118 is gone from tests/fixtures"
    for screen in CAPTURED:
        values = FIXTURES / f"vars_{screen}.txt"
        assert values.exists(), f"wp_{screen}.js has no {values.name}"
        assert facit_path(screen).exists(), (
            f"wp_{screen}.js has no facit: run this file with CTC_WRITE_FACIT=1 and read it through"
        )


def facit_118() -> list[dict]:
    return json.loads(facit_path(118).read_text(encoding="utf-8"))["widgets"]


def test_the_facit_agrees_with_the_older_capture_of_the_same_screen(page):
    """widgets_i255_118.json was decoded at another moment, its captions in Swedish.

    Everything but the captions themselves agrees, widget for widget, except
    widget 8: the compressor's row, which the panel draws as a speed in rps
    while the compressor runs and as a word while it stands. Variable 74 picks
    which, and it stood at 1 in vars_118.txt.
    """
    older = {w["index"]: w for w in page("i255_118")["widgets"]}
    ours = {w["index"]: w for w in facit_118()}
    assert set(ours) == set(older)
    for index, widget in ours.items():
        theirs = older[index]
        for name in ("kind", "x", "y", "width", "height", "visible"):
            assert widget[name] == theirs[name], f"widget {index}: {name}"
        if index == 8:
            continue
        assert widget["value_fmt"] == theirs.get("value_fmt"), f"widget {index}: format"
        assert widget["value_vars"] == theirs.get("value_vars"), f"widget {index}: variables"
        assert (widget["text_id"] is not None) == ("label" in theirs), f"widget {index}: caption"
    assert ours[8]["value_fmt"] == "%.-1frps" and ours[8]["value_vars"] == [37]
    assert "value_fmt" not in older[8] and older[8]["label"] == "Från"


def test_the_text_ids_are_the_entries_the_display_names_them_by(page):
    # Three catalogue entries read off the display itself, one request each:
    # 1807 is "Avgiven värme (kW)", 1804 "Avgiven kyla (kW)" and 1810
    # "Tillförd effekt (kW)". The older capture resolved the same widgets to
    # the same words, so the ids the decoder finds are the entries the panel
    # draws, not merely ids that happen to be stable.
    older = {w["index"]: w.get("label") for w in page("i255_118")["widgets"]}
    known = (1804, 1807, 1810)
    found = {w["text_id"]: older[w["index"]] for w in facit_118() if w["text_id"] in known}
    assert found == {
        1807: "Avgiven värme (kW)",
        1804: "Avgiven kyla (kW)",
        1810: "Tillförd effekt (kW)",
    }


def _widget(web_api, index: int, changes: dict[int, int]):
    widgets = run(client_for(web_api, capture_session(118, changes)).async_widgets(118))
    return next(w for w in widgets if w.index == index)


@pytest.mark.parametrize(
    ("value", "text_id", "fmt"),
    [(0, 92, None), (1, None, "%.-1frps"), (2, 494, None), (3, None, None)],
)
def test_a_variable_picks_the_compressor_rows_alternative(web_api, value, text_id, fmt):
    # Widget 8's group holds a text, a format and a text, [3, 92, 4, 1, 3, 494],
    # and variable 74 counts alternatives: 1 is the format, which the rps
    # reading is drawn with, and 3 runs off the end, where nothing is drawn.
    widget = _widget(web_api, 8, {74: value})
    assert (widget.text_id, widget.value_fmt) == (text_id, fmt)
    assert widget.label == (f"T{text_id}" if text_id is not None else None)


@pytest.mark.parametrize(("value", "text_id"), [(0, 521), (37, 1928), (38, 2240), (39, None)])
def test_a_long_group_is_counted_to_the_alternative_the_variable_names(web_api, value, text_id):
    # Widget 6 is the outdoor unit's model, picked by variable 75 out of 39
    # texts. It stood at 38 when the screen was captured, the last of them,
    # entry 2240: EA720M. The first, the one before and one past the end
    # bracket it.
    assert _widget(web_api, 6, {75: value}).text_id == text_id


def test_the_english_label_is_read_in_english_from_the_same_entry(web_api):
    session = capture_session(118)
    client = client_for(web_api, session)

    async def scenario():
        widgets = await client.async_widgets(118)
        before = session.count("/vars/")
        english = {
            w.index: await client.async_english_label(118, w) for w in widgets if w.text_id
        }
        reread = session.count("/vars/") - before
        # A widget without a caption goes back to the definition, the way a
        # widget built elsewhere does, and finds none there either.
        bare = next(w for w in widgets if w.text_id is None)
        return english, reread, await client.async_english_label(118, bare)

    english, reread, none = run(scenario())
    assert reread == 0, "a caption's English reads no variables again"
    assert none is None
    labelled = [w for w in facit_118() if w["text_id"] is not None]
    assert english == {w["index"]: f"T{w['text_id']}" for w in labelled}
    for widget in labelled:
        assert ("GET", f"/txt/0/{widget['text_id']}", None) in session.calls


def test_the_definition_is_read_once_and_whole(web_api):
    session = capture_session(118)
    client = client_for(web_api, session)

    async def scenario():
        first = await client.async_screen_def(118)
        again = await client.async_screen_def(118)
        await client.async_widgets(118)
        await client.async_widgets(118)
        return first, again

    first, again = run(scenario())
    assert again is first
    assert session.count("/wp/") == 1, "the definition is fetched once a run"
    assert first.index == 118
    assert first.c0 == [0, 0, 480, 1000, 2, 1, 1, 0, 1, 1]
    sizes = (len(first.c1), len(first.v0), len(first.t0), len(first.t1), len(first.t2))
    assert sizes == (95, 216, 285, 95, 51)
    assert first.c1[0] == [3, "p118i0", 0, 1, 2, 3, 4, 5, 6, 6, 7, 8, 0]
    assert first.t2[1] == ["%.-1frps", 0, [1, 37]]


def test_rendering_asks_for_each_text_once(web_api):
    session = capture_session(118)
    client = client_for(web_api, session)

    async def scenario():
        await client.async_widgets(118)
        first = list(session.calls)
        await client.async_widgets(118)
        return first, session.calls[len(first):]

    first, second = run(scenario())
    texts = {w["text_id"] for w in facit_118() if w["text_id"] is not None}
    assert sorted(path for _m, path, _b in first) == sorted(
        ["/wp/118", "/vars/118", "/vars/glob", *(f"/txt/1/{t}" for t in texts)]
    )
    # The values change, so they are read again; the definition and the
    # captions do not.
    assert sorted(path for _m, path, _b in second) == ["/vars/118", "/vars/glob"]


# ------------------------------------------------------------- gzip


def test_a_gzipped_answer_is_unpacked_and_its_bom_dropped(web_api):
    raw = (FIXTURES / "wp_118.js").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "the capture keeps the display's BOM"
    source = run(client_for(web_api, capture_session(118))._request("/wp/118"))
    assert source.startswith("function f118rv(){return p118v}")
    assert source == raw.decode("utf-8")[1:]


def test_an_answer_that_is_not_gzipped_is_read_as_it_is(web_api):
    # The display gzips /wp/ whatever is asked for, and nothing says so but the
    # bytes themselves: the decision is made on gzip's magic number, not on a
    # header (the fake response has none).
    plain = run(client_for(web_api, capture_session(118, plain=True)).async_widgets(118))
    packed = run(client_for(web_api, capture_session(118)).async_widgets(118))
    assert as_facit(plain) == as_facit(packed) == facit_118()


# ----------------------------------------------- a screen built by hand

#: A constructed screen, not a capture, for what screen 118 never draws. Every
#: number below is written by hand, and so is the facit after it.
CONSTRUCTED = 900
#: The globals: [3] is 13, [5] is 0.
CONSTRUCTED_GLOBALS = "1|2026|10|13|12|0|0|"
#: The screen's variables: 0: 4, 1: the serial number as text, 2: text where a
#: number is wanted, 3: 2, 4: 5, 5: 0 (hidden), 6 and 7: hours and minutes.
CONSTRUCTED_VALUES = '4|"720825400001"|"abc"|2|5|0|3|46|'
CONSTRUCTED_V0 = [
    2, 10,    # 0  literal 10
    2, 40,    # 1  literal 40
    2, 200,   # 2  literal 200
    2, 28,    # 3  literal 28
    2, 1,     # 4  literal 1: shown
    1, 5,     # 5  variable 5, which is 0: hidden
    0, 3,     # 6  global 3, which is 13
    1, 1,     # 7  variable 1: the serial number, text
    1, 2,     # 8  variable 2: text, read as a number it is -1
    1, 40,    # 9  variable 40: past the end, -1
    0, 40,    # 10 global 40: past the end, -1
    2, 0,     # 11 literal 0, for the entries the decoder does not read
]
CONSTRUCTED_T0 = [
    2, 0, 0,  # 0 literal 0 picks group 0's only alternative: text 2001
    1, 3, 1,  # 1 variable 3 (2) picks group 1's third alternative: text 2002
    2, 1, 1,  # 2 literal 1 picks group 1's second alternative: format 1
    2, 0, 2,  # 3 group 2 is a format by code 4: format 0
    1, 4, 1,  # 4 variable 4 (5) runs past group 1's three alternatives: nothing
    2, 0, 9,  # 5 there is no group 9: nothing
    0, 5, 1,  # 6 global 5 (0) picks group 1's first alternative: text 2003
]
#: Group 1 mixes a two-slot text, a three-slot format and a two-slot text: a
#: selector that counted slots instead of alternatives lands on nothing.
CONSTRUCTED_T1 = [[3, 2001], [3, 2003, 0, 5, 1, 3, 2002], [4, 0]]
CONSTRUCTED_T2 = [
    ["%02d:%02d", 0, [1, 6, 1, 7]],                   # two variables, as an i550 prints a clock
    ["%.1f°C", 0, [1, 0], "--.-°C", 9999, [None]],    # a reading, and its "no sensor" variant
]
F = 11  # a reference the decoder does not read
CONSTRUCTED_C1 = [
    [3, "p900i0", 0, 0, 2, 3, F, F, 4, F, F, F, 0],    # caption, text 2001
    [1, "p900i1", 1, 6, 3, 3, F, F, 4, F, 1],          # icon at a y from the globals, text 2002
    [3, "p900i2", 0, 1, 1, 3, F, F, 4, F, F, F, 2],    # reading "%.1f°C" of variable 0
    [2, "p900i3", 0, 1, 1, 3, F, F, 5, F, F, F, 3],    # clock of variables 6 and 7, hidden
    [5, "p900i4", 0, 2, 2, 3, F, F, 4, F, F, F, 7],    # string element: the serial number
    [5, "p900i5", 0, 2, 2, 3, F, F, 4, F, F, F, 9],    # string element with no variable
    [3, "p900i6", 8, 9, 10, 3, F, F, 4, F, F, F, 4],   # geometry from text and from nothing
    [3, "p900i7", 0, 0, 2],                            # too short to be a widget
    [3, "p900i8", 0, 0, 2, 3, F, F, 4, F, F, F, 5],    # a group that is not there
    [0, "p900i9", 0, 0, 3, 3, F, F, 4, F],             # kind 0 without a text slot
    [7, "p900i10", 0, 0, 3, 3, F, F, 4, F, F, F, 0],   # a kind that carries no text
    [3, "p900i11", 0, 1, 2, 3, F, F, 4, F, F, F, 6],   # caption picked by a global
]

CONSTRUCTED_FACIT = [
    # index kind  x   y  width height visible text_id label value_fmt value_vars text_value
    (0, 3, 10, 10, 200, 28, True, 2001, "T2001", None, None, None),
    (1, 1, 40, 13, 28, 28, True, 2002, "T2002", None, None, None),
    (2, 3, 10, 40, 40, 28, True, None, None, "%.1f°C", [0], None),
    (3, 2, 10, 40, 40, 28, False, None, None, "%02d:%02d", [6, 7], None),
    (4, 5, 10, 200, 200, 28, True, None, None, None, None, "720825400001"),
    (5, 5, 10, 200, 200, 28, True, None, None, None, None, None),
    (6, 3, -1, -1, -1, 28, True, None, None, None, None, None),
    (8, 3, 10, 10, 200, 28, True, None, None, None, None, None),
    (9, 0, 10, 10, 28, 28, True, None, None, None, None, None),
    (10, 7, 10, 10, 28, 28, True, None, None, None, None, None),
    (11, 3, 10, 40, 200, 28, True, 2003, "T2003", None, None, None),
]


def constructed_source() -> str:
    tables = {
        "t0": CONSTRUCTED_T0, "t1": CONSTRUCTED_T1, "t2": CONSTRUCTED_T2,
        "v0": CONSTRUCTED_V0, "c0": [0, 0, 480, 1000, 2, 1, 1, 0, 1, 1], "c1": CONSTRUCTED_C1,
    }
    body = ",".join(
        f"p{CONSTRUCTED}{name}={json.dumps(table, ensure_ascii=False, separators=(',', ':'))}"
        for name, table in tables.items()
    )
    p = f"p{CONSTRUCTED}"
    return f"\ufefffunction f{CONSTRUCTED}rv(){{return {p}v}}var {p}v=[],{body};"


def constructed_session() -> DisplaySession:
    def answer(method, path, body):
        if path == f"/wp/{CONSTRUCTED}":
            return 200, gzip.compress(constructed_source().encode("utf-8"))
        if path == f"/vars/{CONSTRUCTED}":
            return 200, CONSTRUCTED_VALUES.encode()
        if path == "/vars/glob":
            return 200, CONSTRUCTED_GLOBALS.encode()
        if path.startswith("/txt/"):
            return 200, f"T{path.split('/')[3]}".encode()
        return 404, b""

    return DisplaySession(answer)


def test_the_constructed_screen_decodes_to_its_hand_written_facit(web_api):
    widgets = run(client_for(web_api, constructed_session()).async_widgets(CONSTRUCTED))
    want = [dict(zip(FIELDS, row, strict=True)) for row in CONSTRUCTED_FACIT]
    assert_matches(as_facit(widgets), want)


def test_a_widget_built_elsewhere_finds_its_caption_through_the_definition(web_api):
    # A widget that did not come out of async_widgets carries no text id, and
    # the definition is followed again with the variables the caller hands in.
    # Widget 11's caption is picked by a global variable.
    client = client_for(web_api, constructed_session())
    bare = web_api.Widget(index=11, kind=3, x=10, y=40, width=200, height=28, visible=True)

    async def scenario():
        values = await client.async_vars(CONSTRUCTED)
        globals_ = await client.async_vars("glob")
        return await client.async_english_label(CONSTRUCTED, bare, values, globals_)

    assert run(scenario()) == "T2003"
