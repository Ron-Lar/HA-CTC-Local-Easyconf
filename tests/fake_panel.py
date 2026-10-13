"""A CTC display behind a fake aiohttp session, for the real client to talk to.

test_panel_walk.py's FakePanel stands in for the client: it answers
async_widgets, async_english_label and async_click itself, and the walk it
serves never meets CtcWebClient. That is enough for the walk to the system
information page, but the menu sweep (catalogue.async_discover_pages) leans on
the client for half of its guards: home is the page whose tile reads
"Operation data" in English, the tile is pressed on the icon above its
caption, the back button is never pressed on a page known to be home, and the
root is learnt from the tile. A fake client would have to copy all of that,
and a copy is what drifts. So this FakePanel is the same idea, a menu tree
that writes down every press, grown into a display: it answers the HTTP the
display answers, and CtcWebClient runs against it unchanged, the decoder
included.

    GET  /sm/all             the page map, built from ``pages``
    GET  /vars/menu          the page the panel is showing
    GET  /vars/glob          a date and a time
    GET  /vars/<screen>      the screen's variables, from ``values``
    GET  /wp/<screen>        the screen definition, gzipped like the display's
    GET  /txt/<lang>/<id>    1 is Swedish and 0 English, from ``texts``
    POST /click/<path>       body "x,y": a press, hit-tested against the page

A page is a list of screens and a screen a list of :class:`Spec`, one per
widget, in drawing order. A press lands on the topmost visible widget whose box
holds the point and that has a ``target``, and the panel moves there; a press
on anything else moves nothing. (440, 23) is the panel's own button, outside
any page: back to ``parent`` on a page, the quick menu on the home screen.

The screen definition is written by :func:`encode_screen` in the shapes the
captured screen 118 uses (tests/fixtures/wp_118.js): literal geometry, a text
through a code 3 group, a format through a code 1 group. That the decoder reads
it back as written is checked against the capture itself in
test_menu_sweep.py, so a fake that drifted from the display's format would
fail there before it could make a sweep test pass or fail for the wrong reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
import gzip
import json

#: The panel's own button, top right: back on a page, its own button on home.
BACK = (440, 23)
#: An address from the documentation range, RFC 5737: nothing is ever sent.
HOST = "192.0.2.10"
#: The date and time /vars/glob answers, as the display writes them.
GLOBALS = "1|2026|10|13|12|0|0|0|"


class FakeResponse:
    """The part of an aiohttp response CtcWebClient._request uses."""

    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body = body
        self.status = status

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def read(self) -> bytes:
        return self.body


Answer = Callable[[str, str, str | None], tuple[int, bytes]]


class DisplaySession:
    """The part of an aiohttp session CtcWebClient uses, answered by a callable.

    ``answer(method, path, body)`` returns the status and the raw bytes, or
    raises TimeoutError for a display that does not answer. Every
    request is written down in ``calls`` as (method, path, body).
    """

    def __init__(self, answer: Answer) -> None:
        self._answer = answer
        self.calls: list[tuple[str, str, str | None]] = []

    async def get(self, url: str, timeout: object = None) -> FakeResponse:
        return self._reply("GET", url, None)

    async def post(
        self, url: str, data: bytes | None = None, timeout: object = None
    ) -> FakeResponse:
        return self._reply("POST", url, data.decode() if data is not None else None)

    def _reply(self, method: str, url: str, body: str | None) -> FakeResponse:
        path = "/" + url.split("//", 1)[1].split("/", 1)[1]
        self.calls.append((method, path, body))
        status, payload = self._answer(method, path, body)
        return FakeResponse(payload, status)

    def count(self, prefix: str) -> int:
        """How many requests went to paths starting with ``prefix``."""
        return sum(1 for _method, path, _body in self.calls if path.startswith(prefix))


def gzipped(source: str) -> bytes:
    """A screen definition as the display sends it: a BOM, then gzip over it all."""
    return gzip.compress(("\ufeff" + source).encode("utf-8"))


@dataclass(frozen=True)
class Spec:
    """One widget: what it is, where it stands, what it says, where a press leads.

    ``text`` is the catalogue id its caption is read from, ``fmt`` and
    ``variables`` the format and the screen variables of a reading. A string
    element (kind 5) names the screen variable holding its text in
    ``content``. ``target`` is the page a press inside the box leads to.
    """

    kind: int
    x: int
    y: int
    width: int
    height: int
    text: int | None = None
    fmt: str | None = None
    variables: tuple[int, ...] = ()
    visible: bool = True
    target: int | None = None
    content: int | None = None

    def holds(self, x: int, y: int) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


def encode_screen(screen: int, specs: Sequence[Spec]) -> str:
    """Write a screen definition the way the display serves /wp/<screen>.

    Every reference is a literal (kind 2 in v0), a caption a code 3 group, a
    reading a code 1 group pointing at a t2 entry. A widget with neither has no
    text slot at all; a string element points at its screen variable. The entry
    layout is screen 118's: the text slot at 10 for kinds 0 and 1, at 12 for 2
    and 3.
    """
    v0: list[int] = []
    known: dict[tuple[int, int], int] = {}

    def ref(kind: int, value: int) -> int:
        if (kind, value) not in known:
            known[(kind, value)] = len(v0) // 2
            v0.extend((kind, value))
        return known[(kind, value)]

    t0: list[int] = []
    t1: list[list[object]] = []
    t2: list[list[object]] = []

    def slot(group: list[object]) -> int:
        t1.append(group)
        t0.extend((2, 0, len(t1) - 1))
        return len(t0) // 3 - 1

    filler = ref(2, 0)
    c1: list[list[object]] = []
    for order, spec in enumerate(specs):
        name = f"p{screen}i{order}"
        geometry = [ref(2, spec.x), ref(2, spec.y), ref(2, spec.width), ref(2, spec.height)]
        visible = ref(2, 1 if spec.visible else 0)
        head = [spec.kind, name, *geometry, filler, filler, visible, filler]
        if spec.kind == 5:
            content = ref(1, spec.content if spec.content is not None else 0)
            c1.append([*head, filler, filler, content])
            continue
        text_slot = None
        if spec.text is not None:
            text_slot = slot([3, spec.text])
        elif spec.fmt is not None:
            refs: list[object] = []
            for variable in spec.variables:
                refs.extend((1, variable))
            t2.append([spec.fmt, 0, refs])
            text_slot = slot([1, 0, len(t2) - 1])
        entry = head if spec.kind in (0, 1) else [*head, filler, filler]
        c1.append(entry if text_slot is None else [*entry, text_slot])

    def js(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    p = f"p{screen}"
    tables = ",".join(
        f"{p}{name}={js(table)}"
        for name, table in (
            ("t0", t0), ("t1", t1), ("t2", t2), ("v0", v0),
            ("c0", [0, 0, 480, 1000, 2, 1, 1, 0, 1, 1]), ("c1", c1),
        )
    )
    return (
        f"function f{screen}rv(){{return {p}v}}function f{screen}idx(){{return {screen}}}"
        f'function f{screen}as(n,t){{addScreen(n,"screenFake",{p}c0,{p}c1,{p}v0,{p}t0,'
        f"{p}t1,{p}t2,{p}v,t)}}var {p}v=[],{tables};"
    )


# ----------------------------------------------------------- building blocks


def heading(text: int) -> Spec:
    """The page's title across the top, as screen 118 draws it."""
    return Spec(3, 50, 4, 300, 38, text=text)


def header_icon(text: int, target: int | None = None) -> Spec:
    """The icon at the top left. Its caption is whatever the panel keeps in its
    slot, and on a real page it may well lead out of the menu when pressed."""
    return Spec(1, 5, 4, 38, 38, text=text, target=target)


def caption(text: int, y: int, x: int = 5, width: int = 190) -> Spec:
    return Spec(3, x, y, width, 28, text=text)


def reading(fmt: str, *variables: int, y: int, x: int = 195, width: int = 50) -> Spec:
    return Spec(3, x, y, width, 28, fmt=fmt, variables=variables)


def tile(text: int, x: int, target: int | None) -> list[Spec]:
    """A home screen tile: the icon, which takes the press, and its caption under it."""
    return [Spec(1, x, 65, 79, 79, target=target), Spec(3, x - 16, 150, 111, 22, text=text)]


def tabs(targets: Iterable[int], width: int, y: int = 222, height: int = 45) -> list[Spec]:
    """A strip of equally wide icons along the bottom, each leading to a page."""
    return [Spec(1, n * width, y, width, height, target=t) for n, t in enumerate(targets)]


def centre(spec: Spec) -> tuple[int, int]:
    """Where the sweep presses a widget: the centre of its box."""
    return spec.x + spec.width // 2, spec.y + spec.height // 2


# ------------------------------------------------------------------ the panel


@dataclass
class Press:
    """One press as the panel took it."""

    page: int
    x: int
    y: int
    landed: int
    #: What took the press: "back", "quick menu", the Spec, or None.
    took: object
    #: Whether the click path named the screens of the page being shown,
    #: which is what makes it a press worked out from the page the panel is on.
    aimed: bool


@dataclass
class FakePanel:
    """A display with a menu, which writes down every press and every page shown.

    ``english_missing`` holds catalogue ids the display answers with nothing in
    English, the way a catalogue that is slow for a moment does. ``silent``
    holds paths the display never answers.
    """

    pages: dict[int, list[int]]
    screens: dict[int, list[Spec]]
    texts: dict[int, tuple[str, str]]
    parent: dict[int, int]
    home: int
    start: int
    values: dict[int, list[object]] = field(default_factory=dict)
    quick: int | None = None
    english_missing: frozenset[int] = frozenset()
    silent: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self.page = self.start
        self.presses: list[Press] = []
        self.shown: list[int] = [self.start]
        self.session = DisplaySession(self.answer)

    def client(self, web_api):
        """A real CtcWebClient, talking to this panel."""
        return web_api.CtcWebClient(self.session, HOST)

    # ---- what the display answers

    def answer(self, method: str, path: str, body: str | None) -> tuple[int, bytes]:
        if path in self.silent:
            raise TimeoutError
        if method == "POST" and path.startswith("/click/"):
            return 200, self._click(path, body or "")
        if path == "/sm/all":
            return 200, self._screen_map().encode()
        if path == "/vars/menu":
            return 200, f"{self.page}|0|0|".encode()
        if path == "/vars/glob":
            return 200, GLOBALS.encode()
        if path.startswith("/vars/"):
            screen = int(path.rsplit("/", 1)[1])
            return 200, self._vars(screen).encode()
        if path.startswith("/wp/"):
            screen = int(path.rsplit("/", 1)[1])
            if screen not in self.screens:
                return 404, b""
            return 200, gzipped(encode_screen(screen, self.screens[screen]))
        if path.startswith("/txt/"):
            language, text_id = (int(part) for part in path.split("/")[2:4])
            swedish, english = self.texts.get(text_id, ("", ""))
            if language == 0:
                return 200, ("" if text_id in self.english_missing else english).encode()
            return 200, swedish.encode()
        return 404, b""

    def _screen_map(self) -> str:
        return "".join(
            f"{page}:" + "|".join(str(screen) for screen in screens) + "|"
            for page, screens in self.pages.items()
        )

    def _vars(self, screen: int) -> str:
        fields = []
        for value in self.values.get(screen, []):
            fields.append(f'"{value}"' if isinstance(value, str) else str(value))
        return "|".join(fields) + "|"

    def _click(self, path: str, body: str) -> bytes:
        named = [int(part) for part in path.split("/", 2)[2].split(";")[2:] if part]
        x, y = (int(part) for part in body.split(","))
        before = self.page
        aimed = named == self.pages.get(before, [])
        took = self._take(x, y)
        self.presses.append(Press(before, x, y, self.page, took, aimed))
        if self.page != before:
            self.shown.append(self.page)
        lines = [GLOBALS, f"{self.page}|0|0|"]
        lines.extend(self._vars(screen) for screen in self.pages.get(self.page, []))
        return "\r".join(lines).encode()

    def _take(self, x: int, y: int) -> object:
        if (x, y) == BACK:
            if self.page == self.home:
                if self.quick is not None:
                    self.page = self.quick
                return "quick menu"
            self.page = self.parent.get(self.page, self.page)
            return "back"
        hit = None
        for screen in self.pages.get(self.page, []):
            for spec in self.screens.get(screen, []):
                if spec.visible and spec.target is not None and spec.holds(x, y):
                    hit = spec  # the last one drawn is on top
        if hit is not None:
            self.page = hit.target
        return hit

    # ---- what the tests read

    def press_at_root(self, route: Sequence[tuple[int, int]], root: int) -> int:
        """Replay a recorded route from the root without the client; return the page."""
        self.page = root
        for x, y in route:
            self._take(x, y)
        return self.page

    @property
    def control_presses(self) -> list[Press]:
        """Every press that was not the panel's own button."""
        return [press for press in self.presses if (press.x, press.y) != BACK]
