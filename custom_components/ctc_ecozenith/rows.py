"""Which display rows get an entity, and when (roadmap L3).

The display draws a row for every sensor its software knows of, fitted or not,
and answers for one that is not fitted with a marker, 9999. The brine
temperatures of an air to water heat pump are such rows on an i255, the second
evaporator sensor another, and on an i550 Pro one more: entities that were
unavailable from the day they were made and would be for good. So a row that
reads the marker when the platform is set up, and has never read as a number,
gets no entity yet; the first harvest in which it leaves a number creates it,
through the platform's own async_add_entities, with no reload. A row that has
read as a number once, a true zero included, always gets its entity, since an
outdoor unit switched off for a week reads the marker too.

The registry is tidied on the same occasion, within one rule: an entry for a
row that is no longer on a page the menu still has is removed, a row the
parser folded away or renamed; an entry for a page the menu does not know is
never touched, since that page was not reached and its rows may well be there.
A row that still is on its page is never removed, however long it has read
the marker: its entry is what a customised name or an area lives in, and the
row comes back the day the unit is switched on again.

Free of Home Assistant imports, so sensor.py's decisions can be tested without
an installation.
"""

from __future__ import annotations

import re
from typing import Any, Collection, Iterable, Mapping

from .const import SlowPage, SlowValue

#: A display row's key: the page it is harvested from, then the row's slug.
_DISPLAY_KEY = re.compile(r"^p(\d+)_")


def split_rows(
    pages: Iterable[SlowPage],
    data: Mapping[str, Any] | None,
    numeric: Collection[str],
) -> tuple[list[tuple[SlowPage, SlowValue]], dict[str, tuple[SlowPage, SlowValue]]]:
    """The rows to make entities of now, and the rows that wait for a number.

    A row is made now when its key is in the harvested data, which only ever
    holds numbers, or when it has read as a number before. Everything else,
    the marker and the not yet read alike, waits: a row of a page not reached
    yet is created by the first harvest that reaches it, which is the same
    moment it would first have shown anything.
    """
    present = data or {}
    now: list[tuple[SlowPage, SlowValue]] = []
    pending: dict[str, tuple[SlowPage, SlowValue]] = {}
    for page in pages:
        for value in page.values:
            if value.key in present or value.key in numeric:
                now.append((page, value))
            else:
                pending[value.key] = (page, value)
    return now, pending


def due_rows(pending: Mapping[str, Any], data: Mapping[str, Any] | None) -> list[str]:
    """Of the rows that wait, the keys the harvest has now given a number."""
    present = data or {}
    return [key for key in pending if key in present]


def vanished_display_keys(menu: Iterable[SlowPage], keys: Iterable[str]) -> set[str]:
    """Of these registry keys, the display rows that left a page the menu still has.

    A key names its page, so a page the menu does not know, one the sweep did
    not reach or an installation from before the whole menu was kept, keeps
    every entry it has. A key on a known page that matches none of that page's
    rows is a row the parser no longer builds, and its entry is an orphan. A
    page's rows are the union of what every copy of the page in ``menu`` says,
    so a selected page and its menu copy cannot disagree about a row.
    """
    rows_by_page: dict[int, set[str]] = {}
    for page in menu:
        rows_by_page.setdefault(page.page, set()).update(value.key for value in page.values)
    gone: set[str] = set()
    for key in keys:
        match = _DISPLAY_KEY.match(key)
        if match is None:
            continue
        page = int(match.group(1))
        if page in rows_by_page and key not in rows_by_page[page]:
            gone.add(key)
    return gone
