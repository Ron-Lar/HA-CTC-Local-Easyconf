"""The stored menu: what is kept, and what is harvested after an update."""

from __future__ import annotations

import json
import pathlib
import re


def page(const, number, title="Sida"):
    return const.SlowPage(page=number, title=f"{title} {number}", screens=[number * 10])


def test_the_first_reading_switches_every_page_on(catalogue, const):
    discovered = [page(const, 1), page(const, 2), page(const, 3)]
    menu, selected = catalogue.merge_menu([], [], discovered)
    assert [p.page for p in menu] == [1, 2, 3]
    assert selected == [1, 2, 3]


def test_an_installation_from_before_the_menu_was_kept_gets_everything(catalogue, const):
    """The upgrade case: no version before this one saved the whole menu.

    Older versions offered the tick boxes with nothing ticked, so an old
    installation may be harvesting one page out of five. There is no record of
    anyone having said no to the others, so they are all switched on and can be
    switched off afterwards.
    """
    discovered = [page(const, n) for n in (20, 22, 30, 31, 40)]
    _menu, selected = catalogue.merge_menu([], [30], discovered)
    assert selected == [20, 22, 30, 31, 40]


def test_a_page_switched_off_stays_off(catalogue, const):
    previous = [page(const, 1), page(const, 2), page(const, 3)]
    discovered = [page(const, 1), page(const, 2), page(const, 3)]
    _menu, selected = catalogue.merge_menu(previous, [1, 3], discovered)
    assert selected == [1, 3]


def test_a_page_the_menu_gained_is_harvested(catalogue, const):
    # A new version reads the menu again; a page nobody has said no to is read.
    previous = [page(const, 1), page(const, 2)]
    discovered = [page(const, 1), page(const, 2), page(const, 9)]
    _menu, selected = catalogue.merge_menu(previous, [1], discovered)
    assert selected == [1, 9]


def test_a_page_that_is_gone_is_forgotten(catalogue, const):
    previous = [page(const, 1), page(const, 2)]
    discovered = [page(const, 1)]
    menu, selected = catalogue.merge_menu(previous, [1, 2], discovered)
    assert [p.page for p in menu] == [1]
    assert selected == [1]


# --------------------------------------------- reading the menu again, or trying to


def test_a_menu_from_an_older_version_is_due_until_the_tries_run_out(catalogue):
    assert catalogue.menu_is_due("0.12.3", "0.13.0", 0, 3)
    assert catalogue.menu_is_due("0.12.3", "0.13.0", 2, 3)
    # Spent is spent: a panel that never answers is not walked over and over.
    assert not catalogue.menu_is_due("0.12.3", "0.13.0", 3, 3)
    # This version has already read it.
    assert not catalogue.menu_is_due("0.13.0", "0.13.0", 0, 3)
    # An installation from before the menu was stamped at all is owed a reading.
    assert catalogue.menu_is_due(None, "0.13.0", 0, 3)


def test_a_menu_that_could_not_be_read_is_tried_again_in_the_same_run():
    """VSH kept 0.12.3's menu through the whole of 0.13.0's first run.

    The display answered in 24 ms when it was measured minutes later, so the one
    attempt had simply landed badly, and a flag meant the next attempt waited for
    a restart with nothing but a debug line to show for it.
    """
    source = (
        pathlib.Path(__file__).resolve().parent.parent
        / "custom_components" / "ctc_ecozenith" / "__init__.py"
    ).read_text(encoding="utf-8")
    assert "_MENU_READ" not in source, "spärren ska vara en räknare, inte en flagga"
    assert "await asyncio.sleep(wait)" in source, "inget nytt försök i samma körning"
    giving_up = source.split("async def _async_reread_menu")[1]
    assert "_LOGGER.warning(" in giving_up, "tystnad när försöken är slut"


def test_the_pause_between_two_readings_is_kept_on_the_clock(catalogue):
    interval = 300.0
    # The first reading of a run waits for nothing.
    assert catalogue.menu_wait(None, 1000.0, interval) == 0
    assert catalogue.menu_wait(1000.0, 1060.0, interval) == 240
    assert catalogue.menu_wait(1000.0, 1300.0, interval) == 0
    # A reload mid-pause must not turn the pause into no pause: the clock, not a
    # sleeping task, is what the next run reads.
    assert catalogue.menu_wait(1000.0, 1001.0, interval) == 299
    # Nor a negative wait once the pause is long past.
    assert catalogue.menu_wait(1000.0, 9000.0, interval) == 0


# ------------------------------------------------- the repairs view's texts


def test_every_repair_message_has_its_texts():
    # Without the strings Home Assistant shows the bare key in the repairs view.
    root = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"
    source = (root / "__init__.py").read_text(encoding="utf-8")
    keys = set(re.findall(r'^ISSUE_\w+ = "([a-z_]+)"', source, re.M))
    assert keys, "inga meddelanden hittades i __init__.py"
    # pages_missing is an id only since R12: the issue shows one of two texts
    # under it, pages_unticked or menu_unread, which are checked like the rest.
    assert {"pages_unticked", "menu_unread"} <= keys
    keys.discard("pages_missing")
    for name in ("strings.json", "translations/en.json", "translations/sv.json"):
        issues = json.loads((root / name).read_text(encoding="utf-8")).get("issues", {})
        for key in keys:
            assert key in issues, f"{key} saknas i {name}"
            assert issues[key].get("title") and issues[key].get("description")


# ------------------------------------------------------ is there a new one?


def test_a_released_version_ahead_of_this_one_is_newer(updates):
    assert updates.newer("0.9.0", "0.9.1")
    assert updates.newer("0.9.0", "0.10.0")
    assert updates.newer("v0.8.0", "v0.9.0")


def test_the_same_version_written_differently_is_not_newer(updates):
    assert not updates.newer("0.9.0", "0.9")
    assert not updates.newer("0.9", "0.9.0")
    assert not updates.newer("0.9.0", "v0.9.0")


def test_running_ahead_of_the_release_is_not_out_of_date(updates):
    # The houses run what has not been released yet; nothing to say there.
    assert not updates.newer("0.9.1", "0.9.0")


def test_a_version_that_cannot_be_read_is_never_called_old(updates):
    assert not updates.newer("0.9.0", "latest")
    assert not updates.newer(None, "1.0.0")
    assert not updates.newer("", "")
