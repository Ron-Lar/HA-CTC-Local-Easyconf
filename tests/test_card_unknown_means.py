"""The card's word for an unknown that means nothing has happened yet (F6.5).

Worked in node against the page stub (tests/card_page.js). The page marks such
a row with ``unknown_means``, naming a word in the table it sends as ``states``
(dashboard_views._NONE_YET), the way it marks a 0 that means no limit with
``zero_means``. The card writes the word while the entity is unknown and what
Home Assistant makes of the state otherwise; a row without the mark reads as
it did, a control's unknown is still "ej satt", and a table without the word
leaves the state to Home Assistant, so an older page and a newer card never
show a blank.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

TESTS = Path(__file__).resolve().parent
CARD = TESTS.parent / "custom_components" / "ctc_ecozenith" / "www" / "ctc-ecozenith-card.js"
PAGE = TESTS / "card_page.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

WORDS = {
    "unset": "ej satt",
    "none_alarm": "inget larm ännu",
    "none_event": "ingen händelse ännu",
}
UNKNOWN = {"state": "unknown", "attributes": {}}
ALARM = {"state": "[E017] Givare solpaneler ut", "attributes": {}}


def _run(program: str):
    done = subprocess.run(["node", "-e", program], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_the_pure_helper_words_an_unknown_the_page_has_marked():
    assert _run(f"""
      const c = require({json.dumps(str(CARD))});
      const t = {json.dumps(WORDS)};
      const u = {json.dumps(UNKNOWN)};
      console.log(JSON.stringify([
        c.ownWord("sensor.a", u, t, undefined, "none_alarm"),
        c.ownWord("event.a", u, t, undefined, "none_event"),
        c.ownWord("sensor.a", {json.dumps(ALARM)}, t, undefined, "none_alarm"),
        c.ownWord("sensor.a", {{state: "unavailable", attributes: {{}}}}, t, undefined, "none_alarm"),
        c.ownWord("sensor.a", u, t),
        c.ownWord("sensor.a", u, t, undefined, "none_missing"),
        c.ownWord("number.a", u, t, undefined, "none_alarm"),
      ].map((v) => (v === undefined ? null : v))));
    """) == [
        "inget larm ännu", "ingen händelse ännu", None, None, None, None, "ej satt",
    ]


def test_the_rows_card_writes_the_word_until_something_has_happened():
    items = [
        {"entity": "sensor.last_alarm", "name": "Senaste larm", "unknown_means": "none_alarm"},
        {"entity": "event.events", "name": "Händelser", "unknown_means": "none_event"},
        {"entity": "sensor.plain", "name": "Utetemperatur"},
    ]

    def shown(states):
        return _run(f"""
          const page = require({json.dumps(str(PAGE))});
          const card = page.mountCard({json.dumps(str(CARD))}, "ctc-ecozenith-rows",
            {json.dumps({"rows": items, "states": WORDS})}, {json.dumps(states)},
            {{formatEntityState: (s) => "HA:" + s.state}});
          const values = page.all(card, (e) => e.className === "value");
          console.log(JSON.stringify(values.map((e) => e.textContent)));
        """)

    nothing_yet = {"sensor.last_alarm": UNKNOWN, "event.events": UNKNOWN, "sensor.plain": UNKNOWN}
    assert shown(nothing_yet) == ["inget larm ännu", "ingen händelse ännu", "HA:unknown"]
    # Once something has happened the state is Home Assistant's to word.
    happened = dict(nothing_yet, **{
        "sensor.last_alarm": ALARM,
        "event.events": {"state": "2026-10-09T06:00:00+00:00", "attributes": {}},
    })
    assert shown(happened) == [
        "HA:[E017] Givare solpaneler ut", "HA:2026-10-09T06:00:00+00:00", "HA:unknown",
    ]
