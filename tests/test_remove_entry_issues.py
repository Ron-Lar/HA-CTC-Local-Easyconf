"""Removing an entry takes every notice it had in the repairs view with it (F5.5).

Home Assistant takes none of an integration's issues away when an entry is
removed, and only modbus_busy went: the notice about the menu, the history
page, the serial number and a new release stood on for an entry that no longer
existed, beside a twin when the unit was added again. Read as source, since
__init__.py imports Home Assistant; test_homeassistant_remove_entry.py removes
a real entry under a real core.
"""

from __future__ import annotations

import re

from conftest import COMPONENT

INIT = (COMPONENT / "__init__.py").read_text(encoding="utf-8")

#: The two texts the pages_missing issue can show under its one id (R12).
TEXTS_OF_ONE_ID = {"ISSUE_PAGES_UNTICKED", "ISSUE_MENU_UNREAD"}


def _issue_constants() -> dict[str, str]:
    return dict(re.findall(r'^(ISSUE_[A-Z_]+) = "([a-z_]+)"$', INIT, re.M))


def test_every_issue_id_of_an_entry_is_in_the_list_removal_goes_through():
    constants = _issue_constants()
    listed = re.findall(r"ISSUE_[A-Z_]+", INIT.split("_ENTRY_ISSUES = (")[1].split(")")[0])
    assert set(listed) == set(constants) - TEXTS_OF_ONE_ID
    assert len(listed) == 5


def test_removal_deletes_each_of_them():
    remove = INIT.split("async def async_remove_entry")[1]
    loop = remove.split("for key in _ENTRY_ISSUES:")[1]
    assert loop.lstrip().startswith('ir.async_delete_issue(hass, DOMAIN, f"{entry.entry_id}_{key}")')
