"""The panel's own control and the row tolerance are written down once (L10).

The tap point (440, 23) stood as a literal in the catalogue and the identity
walk beside two named copies, and the row tolerance was set three times over.
Both now live in const.py, and web_api, catalogue and identity import them, so
a change in one place reaches every walk.
"""

from __future__ import annotations

import re

from conftest import COMPONENT

#: An assignment of either name, or the tap point written out, anywhere but
#: in const.py.
REDEFINED = re.compile(r"^\s*_?(ROW_TOLERANCE|CHROME_BUTTON|BACK_BUTTON)\s*[:=]", re.M)
TAP_POINT = re.compile(r"\b440\s*,\s*23\b")


def test_only_const_defines_the_chrome_button_and_the_row_tolerance():
    for path in sorted(COMPONENT.glob("*.py")):
        if path.name == "const.py":
            continue
        text = path.read_text(encoding="utf-8")
        assert not REDEFINED.search(text), path.name
        assert not TAP_POINT.search(text), path.name


def test_the_walkers_use_the_one_definition(const, web_api, catalogue, identity):
    assert const.CHROME_BUTTON == (440, 23)
    for module in (web_api, catalogue, identity):
        assert module.CHROME_BUTTON is const.CHROME_BUTTON, module.__name__
        assert module.ROW_TOLERANCE == const.ROW_TOLERANCE, module.__name__
