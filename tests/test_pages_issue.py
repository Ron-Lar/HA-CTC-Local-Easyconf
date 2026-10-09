"""No page read: a menu read with nothing ticked is told apart from a menu never read (R12).

The repairs view said the same thing either way, and asked for a reload that
changes nothing: the menu tries outlive a reload. c71680d6, an i360, stood with
no page read through several releases, and neither its owner nor the
maintainer could tell from the text whether nothing had been ticked or the
menu had never been read. The issue keeps its id and shows one of two texts,
decided by whether a menu is stored. The run under a real core is in
test_homeassistant_pages_issue.py.
"""

from __future__ import annotations

import json
import pathlib

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
TEXTS = ("strings.json", "translations/en.json", "translations/sv.json")


def _issues(name: str) -> dict:
    return json.loads((COMPONENT / name).read_text(encoding="utf-8"))["issues"]


def test_both_texts_are_there_and_the_old_one_is_gone():
    for name in TEXTS:
        issues = _issues(name)
        assert "pages_missing" not in issues, f"den gamla texten står kvar i {name}"
        for key in ("pages_unticked", "menu_unread"):
            assert issues[key]["title"] and issues[key]["description"], f"{key} i {name}"


def test_the_ticked_text_says_where_to_tick_and_the_unread_one_what_happens_next():
    english = _issues("strings.json")
    assert "Configure" in english["pages_unticked"]["description"]
    unread = english["menu_unread"]["description"]
    assert "Read the display's menu again" in unread, "knappen som läser om menyn nu"
    assert "after every start" in unread, "när den provas av sig själv"
    assert "diagnostics" in unread, "vad man gör när det aldrig lyckas"
    # A reload does not read the menu again: the tries outlive it.
    assert "reload" not in unread.lower()
    swedish = _issues("translations/sv.json")
    assert "Konfigurera" in swedish["pages_unticked"]["description"]
    assert "Läs om displayens meny" in swedish["menu_unread"]["description"]
    assert "ladda om" not in swedish["menu_unread"]["description"].lower()


def test_the_english_texts_are_the_same_and_the_swedish_has_no_dashes():
    assert _issues("strings.json") == _issues("translations/en.json")
    for key in ("pages_unticked", "menu_unread"):
        text = " ".join(_issues("translations/sv.json")[key].values())
        assert "–" not in text and "—" not in text and " - " not in text, key


def test_the_menu_decides_which_text_under_the_one_id():
    # __init__.py imports Home Assistant, so the decision is read as source.
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    body = source.split("def _async_review_issues")[1].split("\n@callback")[0]
    pages = body.split("review(\n        ISSUE_PAGES,")[1].split("review(")[0]
    assert "runtime.web is None" in pages
    assert "ISSUE_PAGES_UNTICKED" in pages and "ISSUE_MENU_UNREAD" in pages
    assert "pages_from_storage(entry.options.get(CONF_MENU))" in pages
    assert "translation_key=text or key" in body
