"""Find the way to one page the reading of the menu does not reach (roadmap L7).

The reading of the menu (catalogue.async_discover_pages) presses every
plausible control of the operation data menu, two levels deep and at most 26
of them, and records the press that reached each page. That is enough on an
i255, whose pages share a tab strip, but not on PT's i550 Pro: its root is a
schematic picture with many places to press, and the budget is spent before
the strip that leads to Kompressor VP1 (page 42, screen 138), the page with
the delivered heat, the power input and the outdoor unit's model. The root
draws the tile that leads there only in some operating states besides.

So the owner names the page, under Configure, and this module looks for the
way to it once. The search starts at the operation data root like the
reading, presses only what the reading would press, and stops at the page:

* at most MAX_TAPS controls tried and at most MAX_DEPTH presses from the
  root, the budget counting every control the first time it is tried on a
  page, as the reading's does; the way back between two tries, the panel's
  own button, the operation data tile and a recorded route replayed, is the
  way around and not a try;
* every press worked out from the page the panel shows, and nothing pressed
  under the heading but the panel's own button, never on the home screen,
  where that button opens the quick menu; nothing on the home screen at all
  but the operation data tile. The way to the home screen is the same walk
  from every page the panel may stand on, and it presses nothing on a page
  that may be the home screen with its tile unread just now: the search
  then ends without the menu, and nothing has been pressed there;
* a control whose caption names Avancerat, Service, Define or the language
  selection (const.NAV_FORBIDDEN) is never pressed, and a page whose heading
  names one of them is never searched: the page asked for is looked at
  before anything is pressed, and a page a tap leads to before anything on
  it is pressed;
* a page a tap leads to is searched only when the panel's own button leads
  from it back to a page of the operation data menu: a page whose way back
  leads anywhere else, the home screen among them, is outside the menu, and
  nothing on it is pressed but that button;
* the panel is put back where it was found, by the route the search
  recorded where the page it started on is one the search reached, the page
  asked for included (catalogue._async_restore).

Two hints decide which page is searched first, and neither presses
anything. Where the owner has walked the panel to the page by hand, the walk
home from it passes the pages above it, and a page on that way is searched
before the others, the nearer to the page asked for the sooner, and a page
being searched is set aside for one found below it on that way. Otherwise a
page nearer the root is searched before one further down, and of pages as
far from the root, one that shares at least two of its places to press with
the page asked for, the strip a set of sibling pages draw alike, before one
that does not. Alike only orders pages at one depth and never sets a page
aside: the page asked for may lie a press from the page being searched, and
a route through a sibling that looks like it would be a press more at every
harvest. Both only order the search: what may be pressed is the same either
way.

The search runs only when the owner starts it, under the panel lock every
walk takes, and what it found is the owner's to keep: the options flow adds
the page to the menu and ticks it when the form is submitted, and nothing is
written otherwise. Nothing here imports Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
import re

from .catalogue import (
    ARRIVED,
    ASTRAY,
    STUCK,
    PanelBusy,
    _async_collect,
    _async_operation_root,
    _async_put_back,
    _async_replay_steps,
    _async_restore,
    _async_tap_targets,
    _pages_by_route,
)
from .const import CHROME_BUTTON, NAV_FORBIDDEN, PAGE_HEADER_HEIGHT, SlowPage
from .web_api import CtcWebClient, CtcWebError, Widget, tap_target

_LOGGER = logging.getLogger(__name__)

#: The search's budget: controls tried, each the first time on its page. The
#: owner decided on 40 (the reading of the menu has 26), with somebody at the
#: panel while it runs.
MAX_TAPS = 40
#: How many presses from the operation data root the page may lie, at most.
MAX_DEPTH = 3

#: How a search ended. FOUND carries the page; every other leaves nothing to save.
FOUND = "found"
#: The display's screen map has no page with that number.
UNKNOWN = "unknown"
#: The home screen, or a page whose heading names a menu the search stays out of.
OUTSIDE = "outside"
#: The home screen, its operation data tile or the root could not be found.
NO_MENU = "no_menu"
#: Not reached within the budget and the depth.
NOT_FOUND = "not_found"
#: The search lost its way: a page whose own button did nothing, or a panel
#: that was somewhere else when the page was to be read.
LOST = "lost"
#: Reached, but the page holds no reading.
EMPTY = "empty"
#: Reached, but a screen or a caption did not answer when it was read.
GAP = "gap"
#: The display stopped answering.
SILENT = "silent"

#: Whether a node of the search is done with, or set aside for a better one.
_DONE = "done"
_SET_ASIDE = "set aside"
#: How often the page asked for is read before a gap is given up on.
_READS = 2
#: A page shares a strip with the page asked for when it has this many of
#: the same places to press.
_STRIP = 2


@dataclass
class PageSearch:
    """What one search for a page came back with."""

    target: int
    reason: str = NOT_FOUND
    #: The page as the reading of the menu describes one, route included,
    #: where the search found it and read it whole.
    page: SlowPage | None = None
    #: The operation data root the route starts from, where it was found.
    root: int | None = None
    #: Controls tried, the budget's count.
    tries: int = 0
    #: Every press the search made on the panel, the way around and the
    #: putting back included.
    presses: int = 0
    #: What the display client said when the display stopped answering.
    error: str | None = None

    @property
    def found(self) -> bool:
        return self.reason == FOUND and self.page is not None


def forbidden(text: str | None) -> bool:
    """Whether a caption or a heading names a menu the search stays out of."""
    folded = (text or "").casefold()
    return any(
        re.search(rf"(?<!\w){re.escape(word.casefold())}(?!\w)", folded)
        for word in NAV_FORBIDDEN
    )


async def _async_verdict(client: CtcWebClient, widget: Widget) -> bool | None:
    """Whether a widget's caption names a forbidden menu, in the panel's language or English.

    None where the caption cannot be read in either, which the search takes
    as a caption it cannot vouch for. A widget without a caption, a box on a
    schematic or a reading, says nothing and is False.
    """
    if widget.text_id is None:
        return False
    texts = (widget.label or "", await client.async_text(widget.text_id, language=0))
    if any(forbidden(text) for text in texts):
        return True
    return False if any(text.strip() for text in texts) else None


async def async_forbidden_page(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> bool | None:
    """Whether a page's heading names a menu the search stays out of; reads, never presses.

    The heading is a text across the top, as async_page_title takes it. True
    where it names one, False where every screen was read and no heading
    does, and None where that cannot be told: a screen whose widgets did not
    come, or a heading whose caption did not. The search does not search a
    page it cannot vouch for.
    """
    unsure = False
    for screen in page_map.get(page, []):
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            unsure = True
            continue
        for widget in widgets:
            if not (
                widget.visible
                and widget.kind in (2, 3)
                and 0 <= widget.y < PAGE_HEADER_HEIGHT
                and widget.width >= 80
            ):
                continue
            verdict = await _async_verdict(client, widget)
            if verdict:
                return True
            unsure = unsure or verdict is None
    return None if unsure else False


async def _async_forbidden_places(
    client: CtcWebClient, widgets: list[Widget]
) -> set[tuple[int, int]]:
    """The places on a screen not to press: a caption that names a forbidden menu, or
    one that cannot be read, and the icon right above it, which is where a tile is
    pressed."""
    banned: set[tuple[int, int]] = set()
    for widget in widgets:
        if not widget.visible or await _async_verdict(client, widget) is False:
            continue
        banned.add(widget.centre)
        if widget.kind in (2, 3):
            banned.add(tap_target(widgets, widget))
    return banned


async def _async_controls(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> list[tuple[int, int]]:
    """The places the reading of the menu would press on a page, less the forbidden ones.

    The same places, in the same order (catalogue._async_tap_targets): never
    under the heading, never the picture a page is drawn on. Of those, the
    places _async_forbidden_places names are left out, worked out from the
    same reading of each screen.
    """

    async def exclude(widgets: list[Widget]) -> set[tuple[int, int]]:
        return await _async_forbidden_places(client, widgets)

    return await _async_tap_targets(client, page_map, page, exclude)


def _misses(client: CtcWebClient) -> int:
    """How many captions the client's text catalogue has not given up so far."""
    return int(getattr(client, "text_misses", 0) or 0)


async def _async_unread_in_english(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> bool:
    """Whether a caption on a page says something in the panel's language and nothing in English.

    The home screen is known by its tile's English caption, so such a
    caption may be that tile, unread just now, as an empty caption is one
    the search cannot vouch for (_async_verdict). The English captions come
    out of the text cache, which holds every answer the look for the tile
    has just had; a screen whose widgets do not come cannot be vouched for
    either.
    """
    for screen in page_map.get(page, []):
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            return True
        for widget in widgets:
            if (
                not widget.visible
                or widget.width <= 0
                or widget.text_id is None
                or not (widget.label or "").strip()
            ):
                continue
            if not (await client.async_text(widget.text_id, language=0)).strip():
                return True
    return False


async def _async_walk_home(
    client: CtcWebClient, page_map: dict[int, list[int]], hops: int = 6
) -> tuple[list[int], bool]:
    """Step back to the home screen as the reading does, noting every page passed.

    The same walk async_goto_home makes, a press of the panel's own button
    at a time, from wherever the panel stands, so the walk costs nothing the
    search would not press anyway. The pages it passes on the way from the
    page the owner walked the panel to are the pages above it, which is
    where the search looks first. Returns them, and whether the home screen
    was reached: a walk that was not is not made a second time.

    The button is never pressed on a page that may be the home screen, where
    it opens the quick menu. The client knows the home screen by its
    operation data tile and keeps it once known, and the client of an entry
    that harvests, like the one the options build for an entry that is not
    loaded, knows the root from the stored menu: one step up from the root
    is the home screen whether its tile can be read just now or not, so the
    walk presses nothing there. And a page on which the
    tile was not found while a caption did not answer, or answered in the
    panel's language and not in English, may be the home screen with its
    tile unread, so the walk stops there too, the page it started on
    included, and the search says it found no menu. Only the root, which is
    never the home screen, is stepped back from all the same.
    """
    trail: list[int] = []
    root = getattr(client, "root", None)
    for _ in range(hops):
        here = await client.async_current_page()
        misses = _misses(client)
        # Asked for no hop, the client says whether the panel is home without
        # pressing anything.
        if await client.async_goto_home(hops=0) is not None:
            return trail, True
        if here in trail or (root is not None and trail and trail[-1] == root):
            return trail, False
        if here != root and (
            _misses(client) > misses or await _async_unread_in_english(client, page_map, here)
        ):
            _LOGGER.debug(
                "Page %s may be the home screen, with its operation data tile unread just "
                "now, so the panel's own button is not pressed there",
                here,
            )
            return trail, False
        trail.append(here)
        await client.async_click(page_map.get(here, []), *CHROME_BUTTON)
        if await client.async_current_page() == here:
            return trail, False
    return trail, await client.async_goto_home(hops=0) is not None


@dataclass
class _Node:
    """A page of the menu to search: how it is reached and how far it has been searched."""

    page: int
    route: list[tuple[int, int]]
    rank: tuple[int, int]
    order: int
    places: list[tuple[int, int]] | None = None
    next: int = 0


class _Search:
    """One search from the operation data root, best page first."""

    def __init__(
        self,
        client: CtcWebClient,
        page_map: dict[int, list[int]],
        target: int,
        found: tuple[int, int],
        known: dict[int, list[tuple[int, int]]],
        trail: list[int],
        hint: set[tuple[int, int]],
        max_taps: int,
        max_depth: int,
    ) -> None:
        self.client = client
        self.page_map = page_map
        self.target = target
        self.root, self.home = found
        #: Every page known to be in the operation data menu, by its route.
        self.known = known
        self.trail = trail
        self.hint = hint
        self.max_taps = max_taps
        self.max_depth = max_depth
        self.outside: set[int] = set()
        self.tries = 0
        self._order = 0

    def _rank(self, page: int, places: list[tuple[int, int]] | None) -> tuple[int, int]:
        """How promising a page is: on the way the owner's panel came home, then alike."""
        near = len(self.trail) - self.trail.index(page) if page in self.trail else 0
        alike = 1 if places is not None and len(set(places) & self.hint) >= _STRIP else 0
        return near, alike

    def _node(self, page: int, route: list[tuple[int, int]], rank: tuple[int, int]) -> _Node:
        self._order += 1
        return _Node(page, route, rank, self._order)

    async def run(self) -> str:
        """FOUND, NOT_FOUND or LOST; the route to the page is then in ``known``."""
        root_places = await _async_controls(self.client, self.page_map, self.root)
        frontier = [self._node(self.root, [], self._rank(self.root, root_places))]
        while frontier and self.tries < self.max_taps:
            # On the owner's way home first, the nearer to the page the
            # sooner; then the shallowest, so a page alike only orders pages
            # as far from the root and never wins a press of depth.
            node = max(frontier, key=lambda n: (n.rank[0], -len(n.route), n.rank[1], -n.order))
            outcome = await self._search(node, frontier)
            if outcome in (FOUND, LOST):
                return outcome
            if outcome == _DONE:
                frontier.remove(node)
        return NOT_FOUND

    async def _regain(self, node: _Node) -> str:
        """Walk the panel back to a page of the search; ARRIVED, ASTRAY or STUCK.

        From a page on the page's own route, the page it was set aside on
        when a page below it looked better, the rest of the route is pressed
        from there; from anywhere else the panel is put back the way the
        restore puts it back (catalogue._async_put_back). Either way every
        step is held to the page it led to when the search found it, which
        ``known`` says by the route: a step that lands anywhere else ends the
        walk before the next step is pressed there, and no step is pressed on
        the home screen.
        """
        here = await self.client.async_current_page()
        if here == node.page:
            return ARRIVED
        above = self.known.get(here)
        if above is not None and len(above) < len(node.route) and (
            node.route[: len(above)] == above
        ):
            landed = await _async_replay_steps(
                self.client, self.page_map, here, node.route, len(above),
                _pages_by_route(self.known), self.home,
            )
            return ARRIVED if landed == node.page else ASTRAY
        return await _async_put_back(
            self.client, self.page_map, node.page, node.route, self.root, self.home, self.known
        )

    async def _search(self, node: _Node, frontier: list[_Node]) -> str:
        """Try the controls of one page until it is done, set aside, or the page is found."""
        while True:
            if await self.client.async_current_page() != node.page:
                regained = await self._regain(node)
                if regained == STUCK:
                    return LOST
                if regained != ARRIVED:
                    _LOGGER.debug(
                        "The route to page %s did not arrive, so the search gives it up", node.page
                    )
                    return _DONE
            if node.places is None:
                # Read on the page itself, so a box the page draws only in
                # some states is a place to press when it is there.
                node.places = await _async_controls(self.client, self.page_map, node.page)
            if node.next >= len(node.places):
                return _DONE
            if self.tries >= self.max_taps:
                return _SET_ASIDE
            x, y = node.places[node.next]
            node.next += 1
            self.tries += 1
            await self.client.async_click(self.page_map.get(node.page, []), x, y)
            landed = await self.client.async_current_page()
            route = [*node.route, (x, y)]
            if landed == self.target:
                self.known[landed] = route
                return FOUND
            if (
                landed == node.page
                or landed == self.home
                or landed in self.known
                or landed in self.outside
            ):
                continue
            child = await self._judge(landed, route)
            if isinstance(child, str):
                return child
            if child is not None:
                frontier.append(child)
                # Set aside only for a page on the owner's way home. A page
                # merely alike waits for the rest of this one: the page asked
                # for may lie a press from here, and a route through a
                # sibling would be a press more at every harvest.
                if child.rank[0] > node.rank[0]:
                    return _SET_ASIDE

    async def _judge(self, page: int, route: list[tuple[int, int]]) -> _Node | str | None:
        """Say whether a page a tap led to is a page of the menu, and whether to search it.

        Read where the panel stands, then one press of the panel's own button,
        which is also the first step of the way back: where it leads says
        whether the page belongs to the operation data menu. A page whose
        heading names a menu the search stays out of, or cannot be read, is
        not searched either. Returns the node to search, None for a page not
        to search, or LOST where the button did nothing.
        """
        # A heading that cannot be read is a page the search cannot vouch for.
        outside = await async_forbidden_page(self.client, self.page_map, page) is not False
        places = None
        if not outside and len(route) < self.max_depth:
            places = await _async_controls(self.client, self.page_map, page)
        await self.client.async_click(self.page_map.get(page, []), *CHROME_BUTTON)
        parent = await self.client.async_current_page()
        if parent == page:
            return LOST
        if outside or parent not in self.known:
            _LOGGER.debug(
                "Page %s is not searched: %s", page,
                "its heading names a menu the search stays out of, or did not answer"
                if outside
                else f"its own button leads to page {parent}, outside the operation data menu",
            )
            self.outside.add(page)
            return None
        self.known[page] = route
        if len(route) >= self.max_depth:
            return None
        return self._node(page, route, self._rank(page, places))


async def _async_read_target(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    target: int,
    route: list[tuple[int, int]],
) -> tuple[str, SlowPage | None]:
    """Read the page the way the reading of the menu does, once more after a gap."""
    for _ in range(_READS):
        if await client.async_current_page() != target:
            return LOST, None
        into: list[SlowPage] = []
        gaps: set[int] = set()
        await _async_collect(client, page_map, target, into, set(), route, gaps)
        if not into:
            return EMPTY, None
        if gaps:
            continue
        if not into[0].values:
            return EMPTY, None
        return FOUND, into[0]
    return GAP, None


async def async_search_page(
    client: CtcWebClient,
    target: int,
    max_taps: int = MAX_TAPS,
    max_depth: int = MAX_DEPTH,
) -> PageSearch:
    """Look for the way to ``target`` from the operation data root, once; see the module."""
    result = PageSearch(target)
    pressed = client.taps
    try:
        page_map = await client.async_screen_map(refresh=True)
        origin = await client.async_current_page()
        if target not in page_map:
            result.reason = UNKNOWN
            return result
        # Nothing is ever pressed on the page asked for, only read, so a
        # heading that cannot be read just now does not stop the search; one
        # that names a forbidden menu does, before anything is pressed.
        if await async_forbidden_page(client, page_map, target) is True:
            result.reason = OUTSIDE
            return result
        # The page's own places to press, from its stored screens: read, not
        # pressed, since the panel is elsewhere.
        hint = set(await _async_tap_targets(client, page_map, target))
    except CtcWebError as err:
        result.reason, result.error = SILENT, str(err)
        return result

    found: tuple[int, int] | None = None
    known: dict[int, list[tuple[int, int]]] = {}
    search: _Search | None = None
    try:
        # Home first, from wherever the panel stands, by the walk that never
        # presses the panel's own button on a page that may be the home
        # screen (_async_walk_home). The pages it passes are a hint only on
        # the way from the page asked for: from anywhere else they are the
        # way from a page the owner did not name.
        trail, home_reached = await _async_walk_home(client, page_map)
        if not home_reached:
            result.reason = NO_MENU
            return result
        if origin != target:
            trail = []
        found = await _async_operation_root(client, page_map, origin)
        if found is None:
            result.reason = NO_MENU
            return result
        root, home = found
        result.root = root
        if target == home:
            result.reason = OUTSIDE
            return result
        known[root] = []
        reason = FOUND
        if target != root:
            search = _Search(
                client, page_map, target, found, known, trail, hint, max_taps, max_depth
            )
            reason = await search.run()
        result.reason = reason
        if reason == FOUND:
            result.reason, result.page = await _async_read_target(
                client, page_map, target, known[target]
            )
    except CtcWebError as err:
        result.reason, result.error = SILENT, str(err)
        result.page = None
    finally:
        if search is not None:
            result.tries = search.tries
        await _async_restore(client, page_map, origin, found, known)
        result.presses = client.taps - pressed
    _say(result)
    return result


def _say(result: PageSearch) -> None:
    """One line in the log on how the search went: the owner started it."""
    if result.found and result.page is not None:
        _LOGGER.info(
            "The search found page %s, %s, %s press(es) from the operation data root, after "
            "%s tries and %s presses in all",
            result.target,
            result.page.title,
            len(result.page.route),
            result.tries,
            result.presses,
        )
    else:
        _LOGGER.info(
            "The search for page %s ended without it (%s) after %s tries and %s presses in all%s",
            result.target,
            result.reason,
            result.tries,
            result.presses,
            f": {result.error}" if result.error else "",
        )


async def async_search_page_locked(client: CtcWebClient, target: int) -> PageSearch:
    """Search under the panel lock every walk takes, or say at once that it is taken.

    As catalogue.async_rescan_pages: a form cannot wait for a harvest to end,
    so a panel that is already taken is answered with PanelBusy, and the look
    and the take happen with nothing awaited in between.
    """
    if client.panel.locked():
        raise PanelBusy
    async with client.panel:
        return await async_search_page(client, target)
