"""The floors the coefficient of performance explanations name are the code's (F3.3).

The lifetime floor went from fifty to ten kilowatt hours in 0.16.0 and the
explanation went on saying fifty, beside a reason under the figure that said
ten. The explanations now take their numbers from cop.py, and this holds the
two to each other for every span that has a floor.
"""

from __future__ import annotations

import re


def _number(pattern: str, text: str) -> int:
    found = re.search(pattern, text)
    assert found, (pattern, text)
    return int(found.group(1))


def test_the_floors_in_the_words_are_the_floors_in_the_code(cop, explanations):
    day = cop.MIN_CONSUMPTION_KWH_DAY
    assert _number(r"minst (\d+) kWh", explanations.explain("cop_day")) == day == 3
    assert _number(r"minst (\d+) kWh", explanations.explain("cop_lifetime")) == cop.MIN_CONSUMPTION_KWH == 10
    week = explanations.explain("cop_week")
    assert _number(r"minst (\d+) kWh per dygn", week) == day
    assert _number(r"alltså (\d+) kWh på en vecka", week) == day * cop.WINDOWS["week"][0] == 21
    month = explanations.explain("cop_month")
    assert _number(r"minst (\d+) kWh per dygn", month) == day
    assert _number(r"alltså (\d+) kWh på en månad", month) == day * cop.WINDOWS["month"][0] == 90
    assert "50 kWh" not in explanations.explain("cop_lifetime")


def test_the_explanation_and_the_reason_under_the_figure_name_the_same_floor(cop, explanations):
    # A machine that has just started counting: 11 against 1 over its life.
    reason = cop.cop_reason(None, "lifetime", 11.0, 1.0, 30)
    assert reason == "för lite energi ännu, 1.0 av 10 kWh"
    assert _number(r"av (\d+) kWh", reason) == _number(r"minst (\d+) kWh", explanations.explain("cop_lifetime"))
    # And for a day.
    reason = cop.cop_reason(None, "day", 2.0, 1.0, 20000)
    assert _number(r"av (\d+) kWh", reason) == _number(r"minst (\d+) kWh", explanations.explain("cop_day"))
