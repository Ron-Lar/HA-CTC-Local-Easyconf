"""The attribute names automations read, in Swedish and in English.

The attributes were named in Swedish, like the entities: skäl, underlag, the
energy figures beside the coefficient of performance, kod, källa and so on. An automation or an energy manager that reads them is tied to
those names, and a name in the page's language is a poor key for code. Each of
them now has an English twin carrying the same value, sent beside it for a
version so that whatever reads the old name has time to move; the Swedish names
go in the next major version, and the README has the table from old to new.

Two twins are not a copy. ``control_active`` is a boolean where ``styrning
aktiv`` says "ja" or "nej", since a key meant for code should not have to be
compared with a Swedish word, and the episodes under ``episodes`` carry English
keys inside as well (:data:`EPISODE_KEYS`), so the list does not have to be
renamed a second time. The values that are text, a reason or what a figure
rests on, stay the integration's Swedish text under both names.

Free of Home Assistant, so the names can be checked without an installation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

#: Every Swedish attribute name with its English twin.
ENGLISH: Final[dict[str, str]] = {
    "skäl": "reason",
    "underlag": "basis",
    "dygn i underlaget": "days_in_basis",
    "avgiven värme kWh": "heat_out_kwh",
    "tillförd energi kWh": "energy_in_kwh",
    "tillförd energi ur": "energy_in_from",
    "kod": "code",
    "källa": "source",
    "sida": "page",
    "skärm": "screen",
    "senast läst": "read_at",
    "senast skriven": "last_written",
    "gäller till": "valid_until",
    "styrning aktiv": "control_active",
    "episoder": "episodes",
}

#: The keys inside each alarm episode, Swedish to English, for ``episodes``.
EPISODE_KEYS: Final[dict[str, str]] = {
    "kod": "code",
    "text": "text",
    "start": "start",
    "slut": "end",
    "utetemperatur": "outdoor_temperature",
}


def with_english(attributes: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The attributes as they are, with each Swedish name's English twin beside it.

    The twin gets the same value, unless the caller has put the English name
    in already, which is how ``control_active`` keeps its own boolean. None,
    and an empty mapping, stay as they are, so an entity with nothing to say
    still says nothing.
    """
    if not attributes:
        return attributes  # type: ignore[return-value]
    result = dict(attributes)
    for name, value in attributes.items():
        english = ENGLISH.get(name)
        if english is not None and english not in result:
            result[english] = value
    return result


def episode_in_english(episode: Mapping[str, Any]) -> dict[str, Any]:
    """One alarm episode as ``episodes`` carries it, under English keys."""
    return {EPISODE_KEYS.get(key, key): value for key, value in episode.items()}
