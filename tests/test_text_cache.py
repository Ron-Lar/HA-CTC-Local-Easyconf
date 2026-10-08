"""The display's text catalogue, as the client caches it.

All navigation rests on one English caption, "Operation data", fetched through
the same cache as every other label. A failure that stayed cached for the rest
of the run therefore cost the harvester its way home until Home Assistant was
restarted, which is what happened on an i255 during a slow minute on
2026-09-18. These tests hold the cache to remembering answers only, and the
English lookup to costing no second reading of the screen.

The fake session serves a real screen captured from an i255, screen 118, and
can be told to let a path time out a number of times.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
SCREEN = 118
#: A catalogue entry screen 118 draws, the heading of the heat pump's page.
HEADING_TEXT = 308


def run(coro):
    return asyncio.run(coro)


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def read(self) -> bytes:
        return self.body


class FakeSession:
    """Answers like the display's web server, and stalls where it is told to.

    ``stall`` maps a path to how many requests for it should time out before it
    answers. The client asks a slow reading for once more with longer patience,
    so making one call fail takes two stalled requests.
    """

    def __init__(self, stall: dict[str, int] | None = None) -> None:
        self.stall = dict(stall or {})
        self.calls: list[str] = []

    async def get(self, url: str, timeout=None) -> FakeResponse:
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append(path)
        if self.stall.get(path, 0) > 0:
            self.stall[path] -= 1
            raise asyncio.TimeoutError()
        if path == f"/wp/{SCREEN}":
            return FakeResponse((FIXTURES / "wp_118.js").read_bytes())
        if path == f"/vars/{SCREEN}":
            return FakeResponse((FIXTURES / "vars_118.txt").read_bytes())
        if path == "/vars/glob":
            return FakeResponse((FIXTURES / "vars_glob.txt").read_bytes())
        if path.startswith("/txt/"):
            language, text_id = path.split("/")[2:4]
            word = "Label" if language == "0" else "Text"
            return FakeResponse(f"{word} {text_id}".encode())
        return FakeResponse(b"", 404)

    def requests_for(self, prefix: str) -> int:
        return sum(1 for path in self.calls if path.startswith(prefix))


class BlankSession(FakeSession):
    """A display whose catalogue answers every text with nothing."""

    async def get(self, url, timeout=None):
        response = await super().get(url, timeout)
        return FakeResponse(b"") if "/txt/" in url else response


@pytest.fixture()
def client_with(web_api):
    def build(stall: dict[str, int] | None = None, session_class=FakeSession):
        session = session_class(stall)
        # A documentation address: nothing is ever sent, the session is fake.
        return web_api.CtcWebClient(session, "192.0.2.10"), session

    return build


# ------------------------------------------------------------ remembering


def test_a_label_that_timed_out_is_asked_for_again(client_with):
    client, session = client_with({f"/txt/1/{HEADING_TEXT}": 2})

    async def scenario():
        first = await client.async_text(HEADING_TEXT)
        second = await client.async_text(HEADING_TEXT)
        return first, second

    first, second = run(scenario())
    assert first == "", "ett misslyckande ger tom sträng, inget undantag"
    assert second == f"Text {HEADING_TEXT}", "andra gången hittas etiketten"
    # Two timed out attempts for the first call, one answer for the second.
    assert session.requests_for(f"/txt/1/{HEADING_TEXT}") == 3


def test_an_answer_is_remembered_and_a_failure_is_not(client_with):
    client, session = client_with({f"/txt/1/{HEADING_TEXT}": 2})

    async def scenario():
        await client.async_text(HEADING_TEXT)   # fails, must not be cached
        await client.async_text(HEADING_TEXT)   # answers, cached
        await client.async_text(HEADING_TEXT)   # served from the cache
        await client.async_text(HEADING_TEXT)

    run(scenario())
    assert session.requests_for(f"/txt/1/{HEADING_TEXT}") == 3


def test_an_empty_answer_is_still_an_answer(client_with):
    client, session = client_with(session_class=BlankSession)

    async def scenario():
        await client.async_text(HEADING_TEXT)
        await client.async_text(HEADING_TEXT)

    run(scenario())
    # Some catalogue entries are blank on purpose; asking twice is a waste.
    assert session.requests_for(f"/txt/1/{HEADING_TEXT}") == 1


def test_a_failed_label_does_not_take_the_screen_with_it(client_with):
    client, _ = client_with({f"/txt/1/{HEADING_TEXT}": 2})
    widgets = run(client.async_widgets(SCREEN))
    heading = next(w for w in widgets if w.text_id == HEADING_TEXT)
    assert heading.label == ""
    # The other captions on the screen were read as usual.
    assert sum(1 for w in widgets if w.label) > 30


# ------------------------------------------------------- the English label


def test_the_english_label_costs_no_second_reading_of_the_screen(client_with):
    client, session = client_with()

    async def scenario():
        widgets = await client.async_widgets(SCREEN)
        readings_before = session.requests_for("/vars/")
        english = {}
        for widget in widgets:
            if widget.label is not None:
                english[widget.index] = await client.async_english_label(SCREEN, widget)
        return widgets, english, readings_before, session.requests_for("/vars/")

    widgets, english, before, after = run(scenario())
    assert before == 2, "renderingen läser skärmens variabler och de globala en gång var"
    assert after == before, "den engelska etiketten läser inte om dem"
    for widget in widgets:
        if widget.label is not None:
            assert widget.text_id is not None
            assert english[widget.index] == f"Label {widget.text_id}"


def test_a_widget_without_a_text_is_not_given_one(client_with):
    client, _ = client_with()

    async def scenario():
        widgets = await client.async_widgets(SCREEN)
        unlabelled = next(w for w in widgets if w.label is None)
        return await client.async_english_label(SCREEN, unlabelled)

    assert run(scenario()) is None


def test_a_widget_built_elsewhere_takes_the_variables_it_is_handed(client_with, web_api):
    # Only a widget that did not come out of async_widgets lacks its text id.
    # Then the screen has to be resolved, with the variables the caller already
    # has rather than two more requests.
    client, session = client_with()

    async def scenario():
        rendered = next(w for w in await client.async_widgets(SCREEN) if w.text_id == HEADING_TEXT)
        values = await client.async_vars(SCREEN)
        globals_ = await client.async_vars("glob")
        bare = web_api.Widget(
            index=rendered.index, kind=rendered.kind,
            x=rendered.x, y=rendered.y, width=rendered.width, height=rendered.height,
            visible=True, label=rendered.label,
        )
        before = session.requests_for("/vars/")
        english = await client.async_english_label(SCREEN, bare, values, globals_)
        return english, session.requests_for("/vars/") - before

    english, extra_readings = run(scenario())
    assert english == f"Label {HEADING_TEXT}"
    assert extra_readings == 0


def test_a_failed_english_label_is_blank_and_tried_again(client_with):
    client, session = client_with({f"/txt/0/{HEADING_TEXT}": 2})

    async def scenario():
        heading = next(w for w in await client.async_widgets(SCREEN) if w.text_id == HEADING_TEXT)
        first = await client.async_english_label(SCREEN, heading)
        second = await client.async_english_label(SCREEN, heading)
        return first, second

    first, second = run(scenario())
    assert first == ""
    assert second == f"Label {HEADING_TEXT}"
    assert session.requests_for(f"/txt/0/{HEADING_TEXT}") == 3
