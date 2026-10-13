"""The README is what HACS shows before anyone installs, and it has to hold (R74).

hacs.json has render_readme, so the README is the integration's shop window:
how to install it comes early, the models it names are the ones it has been
seen on, every card it names exists, no image points at nothing, the Energy
dashboard gets the two counters that belong there and a warning about the one
that does not, under every name the display gives it, and the removal says
what stays behind. A link in the opening, Install or Options leads somewhere
in HACS too, the menu is said to be read in the language it is read in, and a
family flag given as an example is one that can be sent. The licence names its
holder. Each point is held to the code where the code has the answer.
"""

from __future__ import annotations

import json
import re

from conftest import COMPONENT, ROOT

README = (ROOT / "README.md").read_text(encoding="utf-8")


def _section(heading: str) -> str:
    assert f"\n## {heading}\n" in README, heading
    return README.split(f"\n## {heading}\n")[1].split("\n## ")[0]


def _flat(text: str) -> str:
    return " ".join(text.split())


def _manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


def test_install_is_the_first_section():
    headings = re.findall(r"^## (.+)$", README, flags=re.M)
    assert headings[0] == "Install", headings[:3]


def test_install_says_how_through_hacs_by_hand_and_where_it_is_found_afterwards():
    install = _flat(_section("Install"))
    repository = _manifest()["documentation"]
    assert f"`{repository}`" in install, "repots adress för HACS"
    assert "*Custom repositories*" in install and "*Integration*" in install
    assert "default list" in install, "säger att den inte finns i standardlistan än"
    assert "custom_components/ctc_ecozenith" in install
    assert f"{repository}/releases" in install
    assert install.count("restart Home Assistant") >= 2
    assert "*Settings, Devices and services, Add integration*" in install
    assert f"*{_manifest()['name']}*" in install


def test_the_models_named_are_the_ones_seen():
    intro = _flat(README.split("\n## ")[0])
    for model in ("i255", "i550 Pro", "i360"):
        assert model in intro, model
    # Named once on the strength of a shared register map, never seen running.
    for unseen in ("i250", "i350", "i555", "EcoLogic"):
        assert unseen not in README, unseen


def _cards() -> set[str]:
    script = (COMPONENT / "www" / "ctc-ecozenith-card.js").read_text(encoding="utf-8")
    cards = set(re.findall(r'"(ctc-ecozenith-[a-z]+)": CtcEcoZenith', script))
    cards |= set(re.findall(r'SECTION_CARD = "(ctc-ecozenith-[a-z]+)"', script))
    return cards


def test_every_card_the_readme_names_exists():
    cards = _cards()
    assert {"ctc-ecozenith-rows", "ctc-ecozenith-section"} <= cards
    named = set(re.findall(r"ctc-ecozenith-[a-z]+(?![a-z.-])", README))
    assert named <= cards, f"kort som inte finns: {sorted(named - cards)}"


def test_no_image_points_at_nothing():
    repository = _manifest()["documentation"].removeprefix("https://github.com/")
    raw = f"https://raw.githubusercontent.com/{repository}/"
    for target in re.findall(r"!\[[^\]]*\]\(([^)\s]+)", README):
        if target.startswith(raw):
            # HACS shows the README outside GitHub, so an image is linked
            # absolutely, to a file in this repository.
            path = target.removeprefix(raw).split("/", 1)[1]
        else:
            assert "://" not in target, f"bild utanför repot: {target}"
            path = target
        assert (ROOT / path).is_file(), f"bilden finns inte: {target}"


def test_the_energy_dashboard_gets_the_two_counters_that_belong_there(const, cop):
    section = _flat(_section("In the Energy dashboard"))
    source = (COMPONENT / "const.py").read_text(encoding="utf-8")
    for key, register, name in (
        ("compressor_kwh", 62341, "Kompressorenergi"),
        ("immersion_kwh", 62191, "Elpatron energi"),
    ):
        start = f'ModbusSensor("{key}", {register},'
        line = next(line for line in source.splitlines() if start in line)
        assert f'"{name}"' in line and '"kWh"' in line and '"total_increasing"' in line, key
        assert f"**{name}**, register {register}" in section, name
    assert "`total_increasing`" in section
    # The delivered heat counter is in kWh under every name, so Home Assistant
    # offers it beside the two above, and the warning has to name it the way
    # the display does: the newer row, and the older one CTC's manual shows on
    # the i360. Swedish only, since the panel is read in Swedish whatever it is
    # set to (test_the_setup_names_the_language_the_menu_is_read_in).
    word = const.LABEL_ENERGY_OUT_SV.split()[0].casefold()
    older = [name for name in cop._HEAT_NAMES if name.split()[0] == word]
    assert len(older) == 1, cop._HEAT_NAMES
    for name in (const.LABEL_ENERGY_OUT_SV, older[0].capitalize()):
        assert f"*{name}*" in section, name
    assert "Whatever the row is called" in section
    assert "not electricity it has used" in section


def test_the_setup_names_the_language_the_menu_is_read_in():
    """The flow stores Swedish for every entry and reads the menu in Swedish,
    whatever the panel is set to, so the tick boxes hold Swedish titles and
    Setup must not promise the reader's own language (F6.2)."""
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert set(re.findall(r"CONF_LANGUAGE: (\w+)", flow)) == {"LANG_SWEDISH"}
    assert "CtcWebClient(session, self._host, self._web_port, LANG_SWEDISH)" in flow
    setup = _flat(_section("Setup"))
    assert "in Swedish, the language the integration reads the display in" in setup
    assert "your own language" not in README


def test_every_family_example_is_a_family_the_integration_does_not_know(const):
    """A family flag goes out only for a model reported as "other", so an
    example of one names a stem that MODEL_FAMILIES does not have; ezi3xx, the
    i360's, could never be sent (F6.3)."""
    stems = re.findall(r"family_([a-z0-9]+)", README)
    assert stems, "inget family_-exempel"
    for stem in stems:
        assert stem not in const.MODEL_FAMILIES, f"family_{stem} är en känd familj"


def _github_anchors() -> set[str]:
    """The ids GitHub gives the README's headings, code blocks left out."""
    prose = re.sub(r"^```.*?^```", "", README, flags=re.M | re.S)
    anchors = set()
    for heading in re.findall(r"^#{1,6} (.+)$", prose, flags=re.M):
        slug = re.sub(r"[^\w\- ]", "", heading.strip().casefold())
        anchors.add(slug.replace(" ", "-"))
    return anchors


def test_the_links_hacs_shows_first_lead_somewhere():
    """HACS shows the README inside Home Assistant, turns "(#x)" into a link
    to its own page and strips the headings' ids, so a link to a heading on
    the same page goes nowhere there. The opening, Install and Options link to
    the heading on GitHub instead, and every such link names a heading that
    exists (F6.4)."""
    repository = _manifest()["documentation"]
    opening = README.split("\n## ")[0]
    for name, text in (
        ("opening", opening),
        ("Install", _section("Install")),
        ("Options", _section("Options")),
    ):
        assert "](#" not in text, f"länk inom sidan i {name}"
    assert f"]({repository}#setup)" in _section("Install")
    assert f"]({repository}#options)" in _section("Install")
    anchors = _github_anchors()
    linked = re.findall(rf"\]\({re.escape(repository)}#([^)\s]+)\)", README)
    assert len(linked) >= 9, linked
    for anchor in linked:
        assert anchor in anchors, f"ingen rubrik för #{anchor}"


def _remove_entry() -> str:
    """The body of async_remove_entry, up to the next top-level statement."""
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    body = init.split("\nasync def async_remove_entry(", 1)[1]
    return re.split(r"\n(?=\S)", body, maxsplit=1)[0]


def test_the_removal_names_what_stays_behind(const):
    removal = _flat(_section("Removing the integration"))
    card = (COMPONENT / "card.py").read_text(encoding="utf-8")
    assert 'CARD_URL = f"/{DOMAIN}/ctc-ecozenith-card.js"' in card
    assert "Three things stay behind." in removal
    assert "`/ctc_ecozenith/ctc-ecozenith-card.js`" in removal, "resursen som blir kvar"
    assert "*Send anonymous usage statistics* off" in removal, "radering av statistiken"
    # The shared stats.py keeps the installation's random id in a Store of the
    # domain's own, one for every entry, and removing an entry leaves it and
    # asks the server to forget nothing, so the text names the file (F6.5).
    stats = (COMPONENT / "stats.py").read_text(encoding="utf-8")
    init = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    assert 'Store(hass, 1, f"{domain}.stats")' in stats
    assert re.search(r"async_setup_stats\(\s*hass, entry, DOMAIN,", init)
    body = _remove_entry()
    assert "Store(" in body, "async_remove_entry hittades inte"
    assert ".stats" not in body and "forget" not in body
    assert f"`config/.storage/{const.DOMAIN}.stats`" in removal, "statistikens id som blir kvar"
    assert "delete it by hand once the last heat pump is removed" in removal
    assert "config/custom_components/ctc_ecozenith" in removal
    scale = (COMPONENT / "quality_scale.yaml").read_text(encoding="utf-8")
    assert "the three things that stay behind" in scale
    assert f".storage/{const.DOMAIN}.stats" in scale


def test_the_licence_names_its_holder():
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "[yyyy]" not in licence and "[name of copyright owner]" not in licence
    assert "   Copyright 2026 Andreas Reimers\n" in licence
    assert "[Apache License 2.0](LICENSE)" in _section("Licence")
