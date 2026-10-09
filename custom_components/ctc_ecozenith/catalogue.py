"""Discover which pages and values the connected display actually offers.

Screen and text numbering differs between models, an i255 and an i550 Pro do not
agree on either, so nothing here is hard coded. The catalogue is built by
reading the unit's own screen map, screen definitions and text catalogue.

Values are paired with labels geometrically: a value's name is the nearest label
to its left on the same row, falling back to the nearest label above it. That
mirrors how the panel itself is laid out.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .const import PERIOD_MARKERS, SENTINELS, SlowPage, SlowValue
from .web_api import CtcWebClient, CtcWebError, Widget, tap_target

_LOGGER = logging.getLogger(__name__)

# Units that appear inside the display's own format strings.
_UNIT_PATTERN = re.compile(r"(kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)")
_CONVERSION = re.compile(r"%[.\-0-9lu]*[dfsu]")
#: A row the panel prints as hours and minutes, "03:46". One widget with two
#: variables on an i550 Pro; on an i255 two widgets with a ":" between them.
_CLOCK_FORMAT = re.compile(r"^\s*%0?\d*d:%0?\d*d\s*$")
_INTEGER_FORMAT = re.compile(r"^\s*%0?\d*[du]\s*$")
#: The "h:m" at the end of such a row's name says how it is printed, not what
#: the reading is, so it goes the way a unit does.
_CLOCK_SUFFIX = re.compile(r"\s+h:m\s*$")
#: "/24 h" is a period, starts per day; the h belongs to the 24, not to the
#: count, and the name keeps the period without it.
_PERIOD_UNIT = re.compile(r"(/\d+)\s+h\s*$")

_ROW_TOLERANCE = 14
_FLOW_X = -10000
MAX_READINGS_PER_ROW = 4
#: Anything this high up and this wide is the page heading, not a row name.
_HEADER_HEIGHT = 45
#: How far a caption and its reading may sit apart and still be the same row.
_ROW_TOLERANCE = 14


def _decimals(fmt: str) -> float:
    """Return the scale implied by the display's format string."""
    if ".1f" in fmt:
        return 0.1
    if ".-1f" in fmt:
        return 0.1
    if ".2f" in fmt:
        return 0.01
    return 1.0


# A unit at the end of the row name. Not when a "/24" stands right before it:
# "Antal starter /24 h" is a count per day, and the h is the day's.
_LABEL_UNIT = re.compile(
    r"(?<!/\d)(?<!/\d\d)(?<!/\d\d\d)[( ](kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)\)?\s*$"
)


_POSITION_SUFFIX = re.compile(r"\s+\d+$")

#: Rows that count hours since the day the unit was commissioned: "Total
#: drifttid" is the time switched on, "Drifttid total" the compressor's, and in
#: English both are "Total operation time". Neither resets, and the panel does
#: not always print the h.
_HOUR_COUNTERS = ("total drifttid", "drifttid total", "total operation time")
#: Compressor starts since commissioning. The English name follows CTC's manual
#: and is not confirmed against a panel's own text catalogue.
_START_COUNTERS = ("antal starter", "number of starts")


def _base_label(label: str) -> str:
    """Drop the positional suffix added when a row carries several readings."""
    return _POSITION_SUFFIX.sub("", label.strip())


def _is_period(label: str) -> bool:
    """True for a row over a window of time, "/30 dagar" or "/24 h"."""
    text = _base_label(label).casefold()
    return any(marker in text for marker in PERIOD_MARKERS)


def is_lifetime_counter(label: str | None) -> bool:
    """True for a row that has counted since the unit was commissioned.

    Operating hours and compressor starts never go down, so their long term
    statistics are sums, like the energy counters'. The same names over a
    period, "Antal starter /24 h", rise and fall and are not counters.
    """
    text = _base_label(label or "").casefold()
    return text.startswith(_HOUR_COUNTERS + _START_COUNTERS) and not _is_period(text)


def _is_period_count(label: str | None) -> bool:
    """True for starts counted over a window, "Antal starter /24"."""
    text = _base_label(label or "").casefold()
    return text.startswith(_START_COUNTERS) and _is_period(text)


def is_clock_format(fmt: str | None) -> bool:
    """True when a row is printed as hours and minutes, "%02d:%02d"."""
    return bool(fmt) and _CLOCK_FORMAT.match(fmt) is not None


def _implied_unit(label: str) -> str | None:
    """The unit a row carries by what it is, where the panel prints none.

    "Drifttid total" is hours like the "Total drifttid h" above it; the panel
    just leaves the h off that row.
    """
    text = _base_label(label).casefold()
    if text.startswith(_HOUR_COUNTERS) and not _is_period(text):
        return "h"
    return None


def _unit(fmt: str, label: str = "") -> str | None:
    """Extract the unit from the format string, or failing that the label.

    CTC writes the unit inside the format string on some rows, for example
    ``%.-1frps``, and inside the row's name on others, for example
    ``Avgiven värme (kW)``. A row printed as hours and minutes is read as
    minutes, and a counter of hours that prints no unit is still hours.
    """
    if is_clock_format(fmt):
        return "min"
    stripped = _CONVERSION.sub(" ", fmt).replace("%%", " % ")
    match = _UNIT_PATTERN.search(stripped)
    if match:
        return match.group(1)
    match = _LABEL_UNIT.search(_base_label(label))
    if match:
        return match.group(1)
    return _implied_unit(label)


def _clean_label(label: str) -> str:
    """Drop a trailing unit from a row name so it reads well as an entity name.

    The positional suffix is kept, since it is what tells "in" from "out" on a
    row that carries two readings. The "h" of a "/24 h" and the "h:m" of a
    clock row go too: they say how the panel prints the row, and the reading
    carries its own unit.
    """
    suffix = _POSITION_SUFFIX.search(label.strip())
    base = _base_label(label)
    base = _CLOCK_SUFFIX.sub("", base)
    base = _PERIOD_UNIT.sub(r"\1", base)
    cleaned = _LABEL_UNIT.sub("", base).strip(" ()") or base
    return f"{cleaned}{suffix.group(0)}" if suffix else cleaned


#: What a row's key has been built from since the first release: the row name
#: as the panel wrote it, less one trailing unit. Frozen on purpose. The key is
#: the entity's identity, sensor.py builds unique_id from it, and every key that
#: moves leaves an entity behind in the registry with a twin beside it. So the
#: name is free to improve in _clean_label, "Drift /24 h:m" reads "Drift /24",
#: while the key stays what every installation already carries, until keys are
#: built from the row's place on the page instead (roadmap L2).
_KEY_UNIT = re.compile(r"[( ](kWh|l/min|ppm|°C|kW|rps|bar|min|%|A|V|h)\)?\s*$")


def _key_label(label: str) -> str:
    """The name a row's key is made of, built the way the first release built it."""
    suffix = _POSITION_SUFFIX.search(label.strip())
    base = _base_label(label)
    cleaned = _KEY_UNIT.sub("", base).strip(" ()") or base
    return f"{cleaned}{suffix.group(0)}" if suffix else cleaned


def display_state_class(unit: str | None, label: str | None) -> str | None:
    """The state class a display reading should carry, going by its unit and row.

    Energy in kWh is either a lifetime counter, which only grows, or a period
    such as "Avgiven värme/30 dagar", which rises and falls as days leave the
    window. Home Assistant refuses "measurement" for energy: a counter is
    "total_increasing", and a period fits no state class at all, so it gets
    none. Operating hours and compressor starts since commissioning are
    counters as well, with or without a unit. Every other reading with a unit
    is a measurement, and one without a unit is left alone, a firmware version
    is a number too, except starts per day: a count that rises and falls with
    the weather, worth its daily statistics (R38) although it has no unit, and
    one that had them already, from the days the "/24 h" lent it an h.
    """
    if unit == "kWh":
        return None if _is_period(label or "") else "total_increasing"
    if is_lifetime_counter(label):
        return "total_increasing"
    if not unit:
        return "measurement" if _is_period_count(label) else None
    return "measurement"


def _raw_number(raw: list[Any], index: int) -> int | None:
    """One raw variable as an integer, or None when absent or a missing marker."""
    if index >= len(raw):
        return None
    item = raw[index]
    if isinstance(item, bool) or not isinstance(item, int) or item in SENTINELS:
        return None
    return item


def numeric_value(value: SlowValue, raw: list[Any]) -> float | None:
    """Turn a raw variable into a number, honouring CTC's missing markers.

    A clock row holds hours and minutes in two variables and is read as one
    figure in minutes, so "03:46" becomes 226 and can be graphed. The row says
    so by its unit. A menu stored by a version from before the minutes carries
    the same format with no unit, and that row goes on reading its hours, as it
    always has, until the menu has been read again and the unit arrives with
    it: the figure and its unit change together, never one before the other.
    """
    if not value.var_indices:
        return None
    if value.unit == "min" and is_clock_format(value.fmt) and len(value.var_indices) == 2:
        hours, minutes = (_raw_number(raw, index) for index in value.var_indices)
        if hours is None or minutes is None:
            return None
        return float(hours * 60 + minutes)
    item = _raw_number(raw, value.var_indices[0])
    if item is None:
        return None
    return round(item * value.scale, 3)


def _is_caption(widget: Widget) -> bool:
    """True when a widget is a text element, not an icon.

    Icons resolve to whatever entry their selector lands on, which on a
    schematic page is often an unrelated string from a long list such as the
    language names. Only text elements are trusted to name a reading.
    """
    return widget.kind in (2, 3)


def _usable_label(widget: Widget) -> bool:
    text = (widget.label or "").strip()
    if not text or len(text) < 2:
        return False
    # Alarm and info catalogue entries are rendered into status fields; they are
    # never the name of a neighbouring reading.
    if text.startswith("[E") or text.startswith("[I") or text == "* Demo *":
        return False
    return True


def has_conversion(fmt: str) -> bool:
    """True when a format string actually renders a number.

    Entries such as ``" / "`` and ``" , "`` are separators between two readings
    on the same row, not readings of their own.
    """
    return bool(_CONVERSION.search(fmt))


def _label_column(captions: list[Widget]) -> int | None:
    """Where the row names stand.

    Not simply the leftmost thing on the page: a divider or a rule drawn at x=0
    would take that place and then nothing would ever line up. The left edge
    most of the names share is what the layout is built on.
    """
    columns: dict[int, int] = {}
    for caption in captions:
        if caption.x > _FLOW_X:
            columns[caption.x] = columns.get(caption.x, 0) + 1
    if not columns:
        return None
    return min(columns, key=lambda x: (-columns[x], x))


def _is_heading(widget: Widget) -> bool:
    """The page's own title, which stands across the top and names nothing."""
    return 0 <= widget.y < _HEADER_HEIGHT and widget.width >= 80


def _pair_labels(widgets: list[Widget]) -> dict[int, str]:
    """Name each reading after the caption that belongs to it.

    Two layouts have to work. Most pages draw a row at a time, caption on the
    left and reading to its right. Some draw every caption first and then every
    reading, and the rows scrolled out of view all share one off screen y, so
    geometry has nothing left to separate them with. What both have in common is
    the order: the captions come in the order their readings do. So the captions
    in the left hand column queue up and each reading takes the one that has
    waited longest.

    A reading parked far to the left is laid out after the one before it and
    belongs to the same caption, which is how "Brine in/ut" carries two.

    A caption whose text the display would not give up still holds its place in
    the queue: it is a row of the page either way, and skipping it would hand
    its reading the next row's name. Where a caption and a reading are both on
    screen their rows have to agree: a caption left behind owns nothing and is
    passed over, and a caption still to come means this reading has no name.
    """
    # A reading is a text element too, so what makes a caption is that it
    # carries no format of its own.
    captions = [
        w for w in widgets
        if w.visible and _is_caption(w) and w.width > 0 and not w.value_fmt
    ]
    # A reading the panel has hidden still holds its row: the caption above it
    # is spoken for, and skipping it would hand that caption to the next row.
    # Only the visible ones are named, since only those become values.
    values = [w for w in widgets if w.value_fmt and has_conversion(w.value_fmt)]
    if not any(w.visible for w in values):
        return {}

    column = _label_column(captions)
    queue = [
        caption for caption in sorted(captions, key=lambda w: w.index)
        if column is not None and abs(caption.x - column) <= 2 and not _is_heading(caption)
    ]

    groups: list[tuple[Widget | None, list[Widget]]] = []
    for value in sorted(values, key=lambda w: w.index):
        if value.x <= _FLOW_X and groups:
            groups[-1][1].append(value)
            continue
        caption = None
        while queue:
            candidate = queue[0]
            if candidate.y >= 0 <= value.y:
                if candidate.y < value.y - _ROW_TOLERANCE:
                    queue.pop(0)  # a row of its own, without a reading
                    continue
                if candidate.y > value.y + _ROW_TOLERANCE:
                    break  # the caption belongs further down; this one has none
            elif candidate.y >= 0 > value.y:
                # The caption is on a row the panel is drawing and the reading
                # is not, so the caption is a heading over the rows below it.
                queue.pop(0)
                continue
            elif value.y >= 0 > candidate.y:
                # Drawn on the page while every caption left is scrolled away:
                # a figure on a schematic, which no caption names.
                break
            caption = queue.pop(0)
            break
        groups.append((caption, [value]))

    pairing: dict[int, str] = {}
    for caption, members in groups:
        members = [value for value in members if value.visible]
        if not members:
            continue
        # A genuine row holds a handful of readings at most: the widest seen is
        # "Överhettning S/H" with four. More than that is a block that ran past
        # its caption, and guessing would give unrelated numbers the same name.
        if caption is None or len(members) > MAX_READINGS_PER_ROW:
            continue
        if not _usable_label(caption):
            continue
        name = (caption.label or "").strip()
        for position, value in enumerate(members, start=1):
            pairing[value.index] = (
                name if len(members) == 1 else f"{name} {position}"
            ).strip()
    return pairing


def _slug(text: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "_", text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o"))
    cleaned = cleaned.strip("_")
    return cleaned or fallback


async def async_page_title(client: CtcWebClient, screens: list[int]) -> str:
    """Return a human title for a page.

    Operation data pages carry their heading as a wide text across the top. The
    chrome screens have only buttons, and icons resolve to whatever entry their
    selector lands on, so a title candidate has to be both near the top and wide
    enough not to be an icon.
    """
    candidates: list[Widget] = []
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        candidates.extend(
            w
            for w in widgets
            if w.visible
            and _usable_label(w)
            and 0 <= w.y < 45
            and w.width >= 80
        )
    if candidates:
        best = max(candidates, key=lambda w: w.width)
        text = (best.label or "").strip()
        if len(text) >= 3:
            return text
    return f"Sida {screens[0] if screens else '?'}"


#: A reading as the page draws it, before it becomes a value: the row name the
#: pairing gave it, the format string, the variables and the widget's index.
_Reading = tuple[str, str, list[int], int]


def _join_clock_rows(readings: list[_Reading]) -> list[_Reading]:
    """Fold a row's hours and minutes into one reading where they are drawn apart.

    An i550 Pro prints "Drift /24 h:m" with one format, ``%02d:%02d``, and two
    variables. An i255 prints the same row as two integers with a ":" between
    them, which the pairing names "Drift /24 h:m 1" and "... 2". Two readings
    of a clock row make one figure, not two unitless sensors, so the second is
    folded into the first and the pair looks the way the i550 draws it: one
    row named "Drift /24 h:m", and so one key, the one an i550 Pro has always
    had. The i255's two keys with _1 and _2 end here, by design.
    """
    joined: list[_Reading] = []
    for raw_label, fmt, indices, index in readings:
        if joined and _CLOCK_SUFFIX.search(_base_label(raw_label)):
            prev_label, prev_fmt, prev_indices, prev_index = joined[-1]
            if (
                _base_label(prev_label) == _base_label(raw_label)
                and _INTEGER_FORMAT.match(prev_fmt)
                and _INTEGER_FORMAT.match(fmt)
                and len(prev_indices) + len(indices) == 2
            ):
                joined[-1] = (
                    _base_label(raw_label),
                    f"{prev_fmt.strip()}:{fmt.strip()}",
                    prev_indices + indices,
                    prev_index,
                )
                continue
        joined.append((raw_label, fmt, indices, index))
    return joined


async def async_page_values(
    client: CtcWebClient, page: int, screens: list[int]
) -> list[SlowValue]:
    """Describe every formatted value on a page."""
    found: list[SlowValue] = []
    seen: set[str] = set()
    for screen in screens:
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError as err:
            _LOGGER.debug("Skipping screen %s: %s", screen, err)
            continue
        pairing = _pair_labels(widgets)
        readings: list[_Reading] = []
        for widget in widgets:
            if widget.value_fmt is None or not widget.value_vars:
                continue
            if not widget.visible or not has_conversion(widget.value_fmt):
                continue
            raw_label = (pairing.get(widget.index) or f"Värde {widget.index}").strip().rstrip(":")
            readings.append((raw_label, widget.value_fmt, list(widget.value_vars), widget.index))
        for raw_label, fmt, indices, index in _join_clock_rows(readings):
            unit = _unit(fmt, raw_label)
            label = _clean_label(raw_label)
            # The key from the row's own name, never from the cleaned one: the
            # name may get better, the key is what the entity is known by.
            base = _slug(_key_label(raw_label), f"s{screen}_w{index}")
            key = f"p{page}_{base}"
            suffix = 2
            while key in seen:
                key = f"p{page}_{base}_{suffix}"
                suffix += 1
            seen.add(key)
            found.append(
                SlowValue(
                    key=key,
                    label=label,
                    page=page,
                    screen=screen,
                    fmt=fmt,
                    var_indices=indices,
                    unit=unit,
                    scale=_decimals(fmt),
                )
            )
    return found


async def async_discover_pages(
    client: CtcWebClient, require_root: bool = False
) -> list[SlowPage]:
    """Walk the operation data subtree and describe every page it contains.

    The panel moves while this runs and is put back where it started. Only the
    operation data subtree is entered, and only once its root has been found
    and verified. That subtree is read only on every CTC model checked, so a
    tap landing slightly off cannot change a setting; the menus around it are
    not, and a panel left in Avancerat or on a settings page must not be swept.
    So where the root cannot be found, nothing is pressed: the page the panel
    is showing is read as it stands and offered on its own, or, with
    ``require_root``, nothing is offered at all. The two readers that fold a
    reading into a stored menu ask for that, since one page in place of the
    whole menu would be a loss, not a reading.

    Layout differs between models: an i255 puts a tab strip along the bottom, an
    i550 Pro does not. Rather than guess, every plausible control on the root
    page is tried once and the tap that reached each page is recorded, so poll
    time can replay a known route instead of deriving one again. The sweep
    stops the moment it cannot get back to the root, because every tap it
    makes is meant for a page it has verified it is on.
    """
    page_map = await client.async_screen_map(refresh=True)
    origin = await client.async_current_page()
    discovered: list[SlowPage] = []
    visited: set[int] = set()

    try:
        root = await _async_operation_root(client, page_map, origin)
        if root is None:
            if require_root:
                _LOGGER.warning(
                    "Could not find the operation data menu; nothing was pressed and no page was read"
                )
                return []
            _LOGGER.warning(
                "Could not find the operation data menu; reading the page the panel is "
                "showing and pressing nothing"
            )
            here = await client.async_current_page()
            await _async_collect(client, page_map, here, discovered, visited, [])
        else:
            await _async_collect(client, page_map, root, discovered, visited, [])
            await _async_explore(client, page_map, root, discovered, visited)
    finally:
        await _async_restore(client, page_map, origin)

    return [page for page in discovered if page.values]


class PanelBusy(Exception):
    """Somebody else holds the panel: the harvest, the menu re-read or the walk."""


async def async_rescan_pages(client: CtcWebClient) -> list[SlowPage]:
    """Read the menu again for a form, under the harvester's own panel lock.

    The harvest, the menu re-read after an update and the walk to the system
    information page all take ``client.panel`` before they move the display,
    and a "read the menu again" from the options has to take the same lock:
    two walkers on one panel record routes that are wrong, and those routes
    are then saved in the options for good. A form cannot wait for the lock,
    though. A harvest holds the panel for as long as its pages take, and a
    dialog that hangs for minutes is a dialog somebody closes and opens again.
    So a panel that is already taken is answered with :class:`PanelBusy` at
    once and only a free one is walked. The look and the take happen with
    nothing awaited in between, so the harvester cannot slip in between them.
    """
    if client.panel.locked():
        raise PanelBusy
    async with client.panel:
        # A second reading, like the one after an update, only counts from a
        # verified operation data root: a single page in place of the whole
        # menu would be a loss (see async_discover_pages).
        return await async_discover_pages(client, require_root=True)


async def _async_explore(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    root: int,
    into: list[SlowPage],
    visited: set[int],
    max_taps: int = 26,
) -> None:
    """Tap what looks like a control, note where it leads, and go one deeper.

    One level is not always enough. An i255 puts every operation data page one
    tap from the root, an i550 Pro hides the heat pump's own page behind a
    second tab strip, and that page is the one carrying the model, the control
    board's firmware and the delivered heat.

    The sweep ends early the moment the root cannot be regained: from then on
    the panel is on a page nobody chose, and the pages found so far are kept.
    """
    taps, intact = await _async_tap_pages(client, page_map, root, root, into, visited, max_taps)

    # Second level: pages found above that carry a strip of their own.
    for page in [p.page for p in list(into) if p.page != root]:
        if not intact or taps >= max_taps:
            break
        if not await _async_return_to_root(client, page_map, root):
            intact = False
            break
        route = next((p.route for p in into if p.page == page), [])
        if not await _async_replay(client, page_map, route, page):
            continue
        more, intact = await _async_tap_pages(
            client, page_map, page, root, into, visited, max_taps - taps, route
        )
        taps += more
    if not intact:
        _LOGGER.warning(
            "Lost the way back to the operation data menu, so the sweep stopped early; "
            "the pages found so far are kept"
        )
        return
    await _async_return_to_root(client, page_map, root)


async def _async_tap_pages(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    root: int,
    into: list[SlowPage],
    visited: set[int],
    budget: int,
    prefix: list[tuple[int, int]] | None = None,
) -> tuple[int, bool]:
    """Tap every target on one page, recording where each tap led.

    Returns the taps spent and whether the root was still within reach at the
    end. Every tap is made on a page the sweep has just verified it is on: a
    tap that led somewhere is followed by the way back, and when the root
    itself cannot be regained the page's remaining targets are given up rather
    than pressed from wherever the panel ended up. When the root is reached but
    the route to this page is not, this page is given up and the sweep goes on
    from the root.
    """
    targets = await _async_tap_targets(client, page_map, page)
    taps = 0
    for x, y in targets:
        if taps >= budget:
            break
        if await client.async_current_page() != page:
            if not await _async_return_to_root(client, page_map, root):
                return taps, False
            if page != root and not await _async_replay(client, page_map, prefix, page):
                break
        taps += 1
        await client.async_click(page_map.get(page, []), x, y)
        landed = await client.async_current_page()
        if landed == page or landed in visited:
            continue
        await _async_collect(
            client, page_map, landed, into, visited, list(prefix or []) + [(x, y)]
        )
    return taps, True


async def _async_replay(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    route: list[tuple[int, int]] | None,
    page: int,
) -> bool:
    """Replay a recorded route from the root and say whether it arrived."""
    for x, y in route or []:
        await client.async_click(page_map.get(await client.async_current_page(), []), x, y)
    return await client.async_current_page() == page


async def _async_return_to_root(
    client: CtcWebClient, page_map: dict[int, list[int]], root: int
) -> bool:
    """Get back to the operation data root from wherever a tap led.

    Stepping back is enough within the subtree, but a tap on the header can drop
    the panel all the way to the home screen, where the back button does nothing.
    """
    if await _async_back_to(client, page_map, root):
        return True
    try:
        if await client.async_goto_operation_root():
            return await client.async_current_page() == root
    except CtcWebError:
        return False
    return False


async def _async_tap_targets(
    client: CtcWebClient, page_map: dict[int, list[int]], page: int
) -> list[tuple[int, int]]:
    """Return distinct points worth tapping on a page, in reading order."""
    seen: set[tuple[int, int, int, int]] = set()
    targets: list[tuple[int, int, int, int]] = []
    for screen in page_map.get(page, []):
        try:
            widgets = await client.async_widgets(screen)
        except CtcWebError:
            continue
        for widget in widgets:
            if not widget.visible or widget.width < 20 or widget.height < 14:
                continue
            if widget.x < 0 or widget.y < 0:
                continue
            if widget.y < 45:
                continue  # the header carries the clock and the back button
            if widget.width >= 460 and widget.height >= 250:
                continue  # the page background, not a control
            box = (widget.x, widget.y, widget.width, widget.height)
            if box in seen:
                continue
            seen.add(box)
            targets.append(box)
    return [(x + w // 2, y + h // 2) for x, y, w, h in _order_targets(targets)]


def _order_targets(
    targets: list[tuple[int, int, int, int]]
) -> list[tuple[int, int, int, int]]:
    """Try the tab strip first, then everything else.

    A row of equally sized boxes along the bottom is a tab strip, and tabs are
    what actually lead somewhere. The rest of an operation data page is usually
    the schematic, worth trying but not worth trying first: spending the tap
    budget on it is how the history page got missed.
    """
    bottom = [b for b in targets if b[1] > 200]
    widths = {b[2] for b in bottom}
    tabs = sorted(bottom, key=lambda b: b[0]) if len(bottom) >= 3 and len(widths) <= 2 else []
    rest = sorted((b for b in targets if b not in tabs), key=lambda b: (b[1], b[0]))
    return tabs + rest


async def _async_back_to(
    client: CtcWebClient, page_map: dict[int, list[int]], target: int, hops: int = 4
) -> bool:
    """Step back with the chrome button until the target page is showing."""
    for _ in range(hops):
        here = await client.async_current_page()
        if here == target:
            return True
        await client.async_click(page_map.get(here, []), 440, 23)
        if await client.async_current_page() == here:
            return False
    return await client.async_current_page() == target


async def _async_collect(
    client: CtcWebClient,
    page_map: dict[int, list[int]],
    page: int,
    into: list[SlowPage],
    visited: set[int],
    route: list[tuple[int, int]],
) -> None:
    """Describe one page and remember how it was reached."""
    if page in visited:
        return
    visited.add(page)
    screens = page_map.get(page, [])
    if not screens:
        return
    title = await async_page_title(client, screens)
    values = await async_page_values(client, page, screens)
    into.append(
        SlowPage(
            page=page,
            title=title,
            screens=list(screens),
            values=values,
            route=list(route),
        )
    )


async def _async_operation_root(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> int | None:
    """Navigate to the operation data menu and return the page it landed on."""
    if await client.async_goto_operation_root():
        return await client.async_current_page()
    return None


async def _async_restore(
    client: CtcWebClient, page_map: dict[int, list[int]], origin: int
) -> None:
    """Put the panel back on the page it was showing before we started."""
    try:
        if await client.async_step_back_to(origin):
            return
        # Backing out did not get there. If the panel started on the operation
        # data root, walking in from the home screen does.
        if await client.async_goto_operation_root():
            if await client.async_current_page() == origin:
                return
        await client.async_goto_home()
    except CtcWebError:
        _LOGGER.debug("Could not restore the panel to page %s", origin)


def pages_to_storage(pages: list[SlowPage]) -> list[dict[str, Any]]:
    """Serialise the catalogue so it survives a restart without rescanning."""
    return [
        {
            "page": page.page,
            "title": page.title,
            "screens": list(page.screens),
            "route": [list(step) for step in page.route],
            "values": [
                {
                    "key": value.key,
                    "label": value.label,
                    "screen": value.screen,
                    "fmt": value.fmt,
                    "vars": list(value.var_indices),
                    "unit": value.unit,
                    "scale": value.scale,
                }
                for value in page.values
            ],
        }
        for page in pages
    ]


def pages_from_storage(stored: list[dict[str, Any]] | None) -> list[SlowPage]:
    """Rebuild the catalogue saved by :func:`pages_to_storage`."""
    pages: list[SlowPage] = []
    for item in stored or []:
        try:
            page = SlowPage(
                page=int(item["page"]),
                title=str(item.get("title", "")),
                screens=[int(s) for s in item.get("screens", [])],
                route=[(int(step[0]), int(step[1])) for step in item.get("route", [])],
            )
            for raw in item.get("values", []):
                page.values.append(
                    SlowValue(
                        key=str(raw["key"]),
                        label=str(raw.get("label", raw["key"])),
                        page=page.page,
                        screen=int(raw["screen"]),
                        fmt=str(raw.get("fmt", "%d")),
                        var_indices=[int(i) for i in raw.get("vars", [])],
                        unit=raw.get("unit"),
                        scale=float(raw.get("scale", 1.0)),
                    )
                )
            pages.append(page)
        except (KeyError, TypeError, ValueError) as err:
            _LOGGER.debug("Discarding a stored page: %s", err)
    return pages


def menu_is_due(stored_version: str | None, version: str, tries: int, limit: int) -> bool:
    """Whether the whole menu is worth reading again, and whether a try is left.

    The stored menu was parsed by whichever version read it, so a newer one reads
    it again: a newer parser makes sense of rows and pages the older one passed
    over. A display that was busy for a moment is worth another try, but the
    panel is a physical thing, so the tries are counted and they stop.
    """
    return stored_version != version and tries < limit


def menu_wait(last: float | None, now: float, interval: float) -> float:
    """Seconds left of the pause between two readings of the menu.

    On the clock rather than in a sleep, because writing the options reloads the
    entry: a pause that lived only in a sleeping task would be skipped by the very
    reload that a successful identity reading causes, and the panel would be walked
    twice in a row.
    """
    if last is None:
        return 0.0
    return max(0.0, interval - (now - last))


def menu_after_rescan(
    stored_menu: list[SlowPage],
    stored_selection: list[SlowPage],
    discovered: list[SlowPage],
) -> tuple[list[SlowPage], bool]:
    """The menu to offer after "read the menu again", and whether it is fresh.

    A reading that gave nothing is not a menu with no pages on it, it is a
    reading that did not happen: the display was busy, or the operation data
    menu could not be found. Writing it would wipe the menu an earlier reading
    built, and every tick box with it. So the stored menu stands, and since it
    is not fresh the version stamp is left alone and the reading stays owed.
    An installation from before the whole menu was kept has only its harvested
    pages to fall back on.
    """
    if discovered:
        return discovered, True
    return (stored_menu or stored_selection), False


def merge_menu(
    previous_menu: list[SlowPage],
    previous_selection: list[int],
    discovered: list[SlowPage],
) -> tuple[list[SlowPage], list[int]]:
    """Fold a fresh reading of the menu into what the user has already chosen.

    A page switched off stays off, because that was a deliberate choice: every
    harvested page means the panel is walked there and back. Anything the menu
    has not offered before starts on, so a page a new version can use is used
    without a visit to the options, and the first reading switches everything on.
    """
    known = {page.page for page in previous_menu}
    switched_off = known - set(previous_selection)
    selected = [page.page for page in discovered if page.page not in switched_off]
    return discovered, selected
