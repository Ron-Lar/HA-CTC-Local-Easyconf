"""What the walk to the system information page may press, and what it says.

Three gaps closed at once: Service is out of the allow list and out of the
texts that describe the walk, the quick menu branch sleeps until it has been
tried at a real panel, and off the home screen a caption is pressed on its own
centre rather than on the icon above it, because in the i550 Pro's quick menu
that icon is the alarm reset.

The texts in strings.json, the two translations and the README are checked
against the constant, so the promise to the user cannot drift from the code.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re

from test_panel_walk import QUICK, SYSTEM_ROWS, FakePanel, QuickPanel

ROOT = pathlib.Path(__file__).resolve().parent.parent
COMPONENT = ROOT / "custom_components" / "ctc_ecozenith"

#: Two of the allowed labels are the same menu under another model's name, so
#: a text names each menu once.
ALIASES = {"Installer": "Advanced", "Display setup": "Display"}
#: What the panel calls each menu in Swedish.
SWEDISH = {"Advanced": "Avancerat", "Display": "Display", "System information": "Systeminformation"}


def run(coro):
    return asyncio.run(coro)


def menus_named(text: str, lead: str, conjunction: str, stop: str) -> set[str]:
    """The menus a sentence lists between ``lead`` and ``stop``."""
    flat = " ".join(text.split())
    match = re.search(rf"{lead} (.+?), {stop}", flat)
    assert match, f"meningen om vad vandringen trycker på saknas: {flat[:80]}"
    listed = match.group(1).replace(f" {conjunction} ", ", ")
    return {item.strip() for item in listed.split(",")}


# ------------------------------------------------------------ the allow list


def test_service_is_not_on_the_allow_list(const):
    assert "Service" not in const.NAV_ALLOWED_EN
    assert "System information" in const.NAV_ALLOWED_EN


def test_the_texts_name_exactly_the_menus_the_constant_allows(const):
    expected_en = {ALIASES.get(label, label) for label in const.NAV_ALLOWED_EN}
    # A menu the constant allows that the text has no Swedish word for fails
    # here, which is the point: adding to the one means adding to the other.
    expected_sv = {SWEDISH[label] for label in expected_en}

    for name in ("strings.json", "translations/en.json"):
        text = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        sentence = text["options"]["step"]["init"]["data_description"]["visit_system_info"]
        named = menus_named(sentence, "presses nothing but", "and", "checks")
        assert named == expected_en, name

    text = json.loads((COMPONENT / "translations/sv.json").read_text(encoding="utf-8"))
    sentence = text["options"]["step"]["init"]["data_description"]["visit_system_info"]
    assert menus_named(sentence, "trycker bara på", "och", "kontrollerar") == expected_sv

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert menus_named(readme, "presses nothing but", "and", "matched") == expected_en


# --------------------------------------------------------- the quick menu


def test_the_panels_own_button_is_not_pressed_unasked(identity, web_api):
    # An i550 Pro is thought to keep the page behind the home screen's menu
    # button, but nobody has tried that press on a real one yet. Until then the
    # walk goes without the serial number rather than guess.
    panel = QuickPanel(web_api, QUICK, {60: SYSTEM_ROWS}, start=1)
    found = run(identity.async_read_identity_via_panel(panel))
    assert found.is_empty
    assert "menu button" not in panel.pressed
    assert panel.page == 1


def test_the_quick_menu_option_is_off_by_default_and_not_in_the_form(const):
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert "entry.options.get(CONF_TRY_QUICK_MENU, False)" in source
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert const.CONF_TRY_QUICK_MENU not in flow and "CONF_TRY_QUICK_MENU" not in flow, (
        "optionen ska vara dold tills grenen provats vid panelen"
    )


# -------------------------------------------- where on a control to press


class IconPanel(FakePanel):
    """A panel that draws an icon above some captions.

    ``icon_above`` maps a page to {english caption: what the icon does}. On a
    home tile the icon is the touch area and the caption's own centre misses,
    which is "opens". In the i550 Pro's quick menu the icon above the system
    information caption is the alarm reset, which is anything else.
    """

    def __init__(self, *args, icon_above=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon_above = icon_above or {}

    def _box(self, page, english):
        n = [e for e, _s, _t in self.tree[page]].index(english)
        return 10 + n * 100, 60, 80, 40        # x, y, width, height of the icon

    async def async_widgets(self, screen):
        out = await super().async_widgets(screen)
        page = screen // 10
        for n, english in enumerate(self.icon_above.get(page, {})):
            x, y, w, h = self._box(page, english)
            # No label: an icon's own text is nonsense and never names a control.
            out.append(self.web_api.Widget(index=500 + n, kind=1, x=x, y=y,
                                           width=w, height=h, visible=True))
        return out

    async def async_click(self, screens, x, y):
        page = self.page
        for english, does in self.icon_above.get(page, {}).items():
            ix, iy, iw, ih = self._box(page, english)
            target = next(t for e, _s, t in self.tree[page] if e == english)
            if ix <= x <= ix + iw and iy <= y <= iy + ih:
                self.taps.append((page, does))
                if does == "opens":
                    self.page = target
                return []
            if does == "opens" and ix <= x <= ix + iw and abs(y - 110) <= 10:
                # A home tile: the caption's own centre lies outside the touch area.
                self.taps.append((page, f"miss at {x},{y}"))
                return []
        return await super().async_click(screens, x, y)


def test_off_the_home_screen_the_caption_is_pressed_not_the_icon_above_it(identity, web_api):
    tree = {
        1: [("Operation data", "Driftinfo", 20), ("Advanced", "Avancerat", 30)],
        30: [("Display", "Display", 40)],
        40: [("System information", "Systeminformation", 60)],
    }
    panel = IconPanel(web_api, tree, {60: SYSTEM_ROWS}, start=1,
                      icon_above={40: {"System information": "Återställ larm"}})
    found = run(identity.async_read_identity_via_panel(panel))
    assert "Återställ larm" not in panel.pressed
    assert found.serial == "720825408489"


def test_on_the_home_screen_the_icon_above_the_caption_is_still_the_tile(identity, web_api):
    # The home tiles are where the icon rule came from: the caption's centre
    # falls outside the touch area, so pressing it there would stop the walk
    # before it began.
    tree = {
        1: [("Operation data", "Driftinfo", 20), ("Advanced", "Avancerat", 30)],
        30: [("Display", "Display", 40)],
        40: [("System information", "Systeminformation", 60)],
    }
    panel = IconPanel(web_api, tree, {60: SYSTEM_ROWS}, start=1,
                      icon_above={1: {"Advanced": "opens"}})
    found = run(identity.async_read_identity_via_panel(panel))
    assert "opens" in panel.pressed
    assert found.serial == "720825408489"
