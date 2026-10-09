"""Alarms as the display prints them: the code, the text, and ten episodes.

Modbus says only that the heat pump is off because of an alarm, code 7 in
register 62017, without which one or since when. The display prints the alarm
itself out of its own catalogue, "[E017] Givare solpaneler ut", and on the
pages captured so far it draws that text in the header icon at the top left;
the status field of a heat pump page is the other text the panel fills from a
variable. This module reads those two places off the values the harvest has
already fetched, through the client (web_api.async_alarm_candidates), keeps
the alarm the panel showed last, and remembers the last ten episodes: when
each began and ended, its code, and the outdoor temperature when it began.

Nothing is polled for this, nothing is navigated for it, the catalogue is
never enumerated, and none of it goes to the statistics backend. Free of Home
Assistant imports; the store is handed in, as for cop.CopTracker.

Where the panel writes the alarm has been seen on one page of one model so
far, the history page of an i550 Pro on 2026-10-05, and is to be confirmed
the next time a unit alarms.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Iterable, Mapping

from .const import ALARM_EPISODES, ALARM_TEXT_PREFIX, SlowPage
from .web_api import CtcWebError

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

#: How long a change to the log may wait before it is written down. Short: an
#: alarm is the kind of thing worth having on disk before the next restart.
SAVE_DELAY_SECONDS = 5

#: "[E017] Givare solpaneler ut": the code in brackets, the text after it.
_ALARM = re.compile(r"^\[(E[^\]]*)\]\s*(.*)$")


def parse_alarm(text: str | None) -> tuple[str, str] | None:
    """The code and the text of an alarm as the panel prints it, or None.

    "[E017] Givare solpaneler ut" gives ("E017", "Givare solpaneler ut"). An
    information text, "[I...]", and anything else is no alarm.
    """
    if not text:
        return None
    match = _ALARM.match(text.strip())
    if match is None:
        return None
    return match.group(1).strip(), match.group(2).strip()


def alarm_text(candidates: Iterable[str | None]) -> str | None:
    """The first candidate that is an alarm text, as the panel printed it."""
    for text in candidates:
        if text and text.strip().startswith(ALARM_TEXT_PREFIX) and parse_alarm(text):
            return text.strip()
    return None


class AlarmWatch:
    """What each harvested page said about an alarm, and which of it is new.

    The harvest notes every page it reads; the log, which lives on the
    runtime, asks once per harvest what was read since it last asked. Judging
    per harvest rather than per page is what keeps an alarm from flapping
    should the panel print it on some pages and not on others: one page
    showing it is an alarm, no page read this round showing it is none.
    """

    def __init__(self) -> None:
        self._notes: dict[int, tuple[str | None, datetime]] = {}
        self._handed: dict[int, datetime] = {}

    async def async_note(
        self,
        client: Any,
        page: SlowPage,
        values_by_screen: Mapping[int, list[Any]],
        read_at: datetime,
    ) -> str | None:
        """Read a page's alarm candidates off the values just harvested.

        Only the screens that carry one of the page's own rows are looked at:
        the chrome screens a page shares with every other hold the clock and
        the back button, not the header icon. A screen whose definition the
        display would not give up leaves the page unnoted this round, which is
        no information rather than "no alarm".
        """
        screens = [
            screen
            for screen in page.screens
            if screen in values_by_screen and any(v.screen == screen for v in page.values)
        ]
        if not screens:
            return None
        read = getattr(client, "async_alarm_candidates", None)
        if read is None:
            # A client that cannot read texts, a stand-in among them: no
            # information, not "no alarm".
            return None
        candidates: list[str] = []
        for screen in screens:
            try:
                candidates.extend(await read(screen, values_by_screen[screen]))
            except CtcWebError as err:
                _LOGGER.debug("Alarm texts of screen %s not read: %s", screen, err)
                return None
        text = alarm_text(candidates)
        self._notes[page.page] = (text, read_at)
        return text

    def fresh(self) -> tuple[bool, str | None]:
        """Whether any page was read since the last call, and the alarm shown.

        The alarm is the first text among the pages read since, in page order;
        a page that was not reached this round does not count, in either
        direction.
        """
        new = {
            page: text
            for page, (text, at) in self._notes.items()
            if self._handed.get(page) != at
        }
        for page in new:
            self._handed[page] = self._notes[page][1]
        if not new:
            return False, None
        return True, next((text for text in new.values() if text), None)


def _stamp(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


class AlarmLog:
    """The alarm the panel shows, and the last ten episodes of it.

    An episode begins when the panel shows an alarm text and no episode with
    that code is open, and ends when a round of the harvest shows none, or
    another code. Episodes are kept newest first, each with the code, the text,
    the panel's own print of it, when it began and ended, and the outdoor
    temperature when it began.
    """

    def __init__(self, store: Any) -> None:
        self._store = store
        self.episodes: list[dict[str, Any]] = []

    async def async_load(self) -> None:
        stored = await self._store.async_load()
        if not isinstance(stored, Mapping) or not isinstance(stored.get("episodes"), list):
            return
        episodes: list[dict[str, Any]] = []
        for item in stored["episodes"]:
            if not isinstance(item, Mapping) or not item.get("code") or not item.get("start"):
                continue
            outdoor = item.get("outdoor")
            episodes.append(
                {
                    "code": str(item["code"]),
                    "text": str(item.get("text") or ""),
                    "shown": str(item.get("shown") or f"[{item['code']}] {item.get('text') or ''}".strip()),
                    "start": str(item["start"]),
                    "end": str(item["end"]) if item.get("end") else None,
                    "outdoor": float(outdoor) if isinstance(outdoor, (int, float)) and not isinstance(outdoor, bool) else None,
                }
            )
        self.episodes = episodes[:ALARM_EPISODES]

    @property
    def latest(self) -> dict[str, Any] | None:
        """The newest episode, open or closed."""
        return self.episodes[0] if self.episodes else None

    @property
    def active(self) -> bool:
        latest = self.latest
        return latest is not None and latest["end"] is None

    def note(self, shown: str | None, when: datetime, outdoor: float | None) -> bool:
        """Take in what a round of the harvest showed. Returns whether anything changed.

        ``shown`` is the alarm text as the panel printed it, or None when the
        round showed no alarm. A round that read nothing is not to be noted at
        all; that is the watch's business.
        """
        parsed = parse_alarm(shown)
        open_episode = self.latest if self.active else None
        changed = False
        if open_episode is not None and (parsed is None or parsed[0] != open_episode["code"]):
            open_episode["end"] = _stamp(when)
            changed = True
            open_episode = None
        if parsed is not None and open_episode is None:
            code, text = parsed
            self.episodes.insert(
                0,
                {
                    "code": code,
                    "text": text,
                    "shown": (shown or "").strip(),
                    "start": _stamp(when),
                    "end": None,
                    "outdoor": float(outdoor)
                    if isinstance(outdoor, (int, float)) and not isinstance(outdoor, bool)
                    else None,
                },
            )
            del self.episodes[ALARM_EPISODES:]
            changed = True
        if changed:
            self._save()
        return changed

    def _save(self) -> None:
        try:
            self._store.async_delay_save(
                lambda: {"episodes": [dict(e) for e in self.episodes]}, SAVE_DELAY_SECONDS
            )
        except Exception as err:  # noqa: BLE001 - a lost save costs the log, not the alarm
            _LOGGER.debug("Could not schedule saving the alarm log: %s", err)

    def attributes(self) -> list[dict[str, Any]]:
        """The episodes as an entity shows them, newest first, in the page's words."""
        return [
            {
                "kod": episode["code"],
                "text": episode["text"],
                "start": episode["start"],
                "slut": episode["end"],
                "utetemperatur": episode["outdoor"],
            }
            for episode in self.episodes
        ]
