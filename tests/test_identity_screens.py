"""The identity is read from the two screens it lives on, once they are known (R9).

So long as any of the six fields was missing, the identity was read at every
start by asking the display for the values of every screen in its map, 154 on
an i255, to shortlist the system information and heat pump screens by
fingerprint. Now the two screens are kept in the options once found, and only
they are read while a field is still missing; the sweep runs for a screen
that is unknown, once per run, and the walk to the system information page
writes down the screen it read as well.
"""

from __future__ import annotations

import asyncio
import pathlib

import pytest

from test_panel_walk import SYSTEM_ROWS, TREE, FakePanel

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

SYSTEM = 52
HEATPUMP = 118
FIRMWARE = 20260522


def run(coro):
    return asyncio.run(coro)


class Display:
    """A display with many screens, two of which carry the identity.

    The system screen answers three strings beside its labels, the heat pump
    screen a date coded firmware beside "Software HP PCB" and the model as a
    caption beside "Model". Every reading of a screen's values is written down.
    """

    def __init__(self, web_api, system=SYSTEM, heatpump=HEATPUMP, screens=150) -> None:
        self.web_api = web_api
        self.system = system
        self.heatpump = heatpump
        self.screens = list(range(1, screens + 1))
        self.vars_read: list[int] = []

    async def async_screen_map(self, refresh=False):
        return {n: [n] for n in self.screens}

    async def async_vars(self, screen):
        self.vars_read.append(screen)
        if screen == self.system:
            return ["720825400001", "02:00:00:12:34:56", "20260610", "1.7"]
        if screen == self.heatpump:
            return [FIRMWARE, 5, 7]
        return [1, 2, 3]

    async def async_widgets(self, screen, values=None, globals_=None):
        widget = self.web_api.Widget
        if screen == self.system:
            out = []
            for n, (swedish, _english, text) in enumerate(SYSTEM_ROWS):
                out.append(widget(index=n, kind=2, x=10, y=40 + n * 30, width=120, height=20,
                                  visible=True, label=swedish))
                out.append(widget(index=100 + n, kind=5, x=200, y=40 + n * 30, width=200,
                                  height=20, visible=True, text_value=text))
            return out
        if screen == self.heatpump:
            return [
                widget(index=0, kind=2, x=10, y=40, width=100, height=20, visible=True, label="Modell"),
                widget(index=1, kind=2, x=200, y=40, width=100, height=20, visible=True, label="EA720M"),
                widget(index=2, kind=2, x=10, y=70, width=160, height=20, visible=True,
                       label="Programversion VP-styrkort"),
                widget(index=3, kind=3, x=200, y=70, width=100, height=20, visible=True,
                       value_fmt="%d", value_vars=[0]),
            ]
        return []

    async def async_english_label(self, screen, widget, values=None, globals_=None):
        if screen == self.system and widget.index < 100:
            return SYSTEM_ROWS[widget.index][1]
        if screen == self.heatpump:
            return {0: "Model", 2: "Software HP PCB"}.get(widget.index)
        return None


# ------------------------------------------------------------- the sweep


def test_without_known_screens_the_sweep_finds_them_and_says_where(identity, web_api):
    display = Display(web_api)
    screens = identity.IdentityScreens()
    found = run(identity.async_read_identity(display, screens))
    assert found.serial == "720825400001"
    assert found.heatpump_model == "EA720M"
    assert found.heatpump_firmware == str(FIRMWARE)
    assert (screens.system, screens.heatpump) == (SYSTEM, HEATPUMP)
    # The sweep: every screen's values, once.
    assert sorted(set(display.vars_read)) == display.screens


def test_with_known_screens_only_they_are_read(identity, web_api):
    display = Display(web_api)
    screens = identity.IdentityScreens(system=SYSTEM, heatpump=HEATPUMP)
    found = run(identity.async_read_identity(display, screens))
    assert found.serial == "720825400001" and found.heatpump_model == "EA720M"
    assert set(display.vars_read) <= {SYSTEM, HEATPUMP}, "inget svep över 150 skärmar"
    assert len(display.vars_read) <= 2


def test_without_a_sweep_an_unknown_screen_is_left_alone(identity, web_api):
    display = Display(web_api)
    found = run(identity.async_read_identity(display, identity.IdentityScreens(), sweep=False))
    assert found.is_empty
    assert display.vars_read == []


def test_a_known_screen_that_left_the_map_counts_as_unknown(identity, web_api):
    # After a firmware update the numbering may differ: a stored screen that is
    # no longer in the map is not read blind, the sweep finds the new one.
    display = Display(web_api)
    screens = identity.IdentityScreens(system=999, heatpump=HEATPUMP)
    found = run(identity.async_read_identity(display, screens))
    assert found.serial == "720825400001"
    assert screens.system == SYSTEM
    assert 999 not in display.vars_read


def test_a_screen_whose_every_field_is_known_is_not_read(identity, web_api):
    display = Display(web_api)
    screens = identity.IdentityScreens(system=SYSTEM, heatpump=HEATPUMP)
    found = run(identity.async_read_identity(display, screens, need_system=False))
    assert found.serial is None and found.heatpump_model == "EA720M"
    assert SYSTEM not in display.vars_read


def test_the_sweep_only_looks_for_what_is_missing(identity, web_api):
    display = Display(web_api)
    screens = identity.IdentityScreens(system=SYSTEM)
    found = run(identity.async_read_identity(display, screens, need_system=False))
    assert found.heatpump_model == "EA720M"
    assert screens.heatpump == HEATPUMP
    # The sweep passes the system screen like any other, but does not read it
    # for the identity: nothing of it was asked for.
    assert found.serial is None and screens.system == SYSTEM


# ------------------------------------------------------------ the values


def test_the_screens_survive_the_options(identity):
    screens = identity.IdentityScreens(system=52)
    assert screens.as_dict() == {"system": 52}
    back = identity.IdentityScreens.from_dict({"system": 52, "heatpump": "118", "x": 1})
    assert (back.system, back.heatpump) == (52, None), "bara heltal tas"
    assert identity.IdentityScreens.from_dict(None).as_dict() == {}
    assert identity.IdentityScreens.from_dict({"system": True}).system is None


def test_which_screen_still_has_something_to_give(identity):
    whole = identity.Identity(
        serial="720825400001", mac="m", display_firmware="d", bootloader="b",
        heatpump_model="EA720M", heatpump_firmware="20260522",
    )
    assert not whole.needs_system_screen and not whole.needs_heatpump_screen
    no_hp = identity.Identity(serial="720825400001", mac="m", display_firmware="d", bootloader="b")
    assert not no_hp.needs_system_screen and no_hp.needs_heatpump_screen
    assert identity.Identity().needs_system_screen


# -------------------------------------------------------------- the walk


def test_the_walk_writes_down_the_screen_it_read(identity, web_api):
    panel = FakePanel(web_api, TREE, {60: SYSTEM_ROWS}, start=20)
    screens = identity.IdentityScreens()
    found = run(identity.async_read_identity_via_panel(panel, screens=screens))
    assert found.serial == "720825400001"
    assert screens.system == 600, "skärmen på sidan Systeminformation"


def test_a_walk_that_finds_nothing_writes_nothing_down(identity, web_api):
    panel = FakePanel(web_api, {1: [("Advanced", "Avancerat", 30)], 30: []}, start=1)
    screens = identity.IdentityScreens()
    run(identity.async_read_identity_via_panel(panel, screens=screens))
    assert screens.as_dict() == {}


# ------------------------------------------------------------- in code


def test_the_catch_up_reads_the_known_screens_and_keeps_new_ones():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    catch_up = source.split("async def _async_catch_up")[1].split("\ndef ")[0]
    assert "IdentityScreens.from_dict(entry.options.get(CONF_IDENTITY_SCREENS))" in catch_up
    # Both screens at every start since R21, a complete identity included, so a
    # firmware update reaches the device; the sweep still runs once per run.
    assert "async_read_identity(client, screens, sweep=sweep)" in catch_up
    assert "if not identity.is_complete" not in catch_up
    assert "sweep=sweep" in catch_up and "_SWEPT" in catch_up
    assert "changed[CONF_IDENTITY_SCREENS]" in catch_up
    # The walk writes its screen into the same record.
    assert "screens=screens" in catch_up
