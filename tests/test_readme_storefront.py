"""The README is what HACS shows before anyone installs, and it has to hold (R74).

hacs.json has render_readme, so the README is the integration's shop window:
how to install it comes early, the models it names are the ones it has been
seen on, every card it names exists, no image points at nothing, the Energy
dashboard gets the two counters that belong there and a warning about the one
that does not, and the removal says what stays behind. The licence names its
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


def test_the_energy_dashboard_gets_the_two_counters_that_belong_there():
    section = _flat(_section("In the Energy dashboard"))
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    for key, register, name in (
        ("compressor_kwh", 62341, "Kompressorenergi"),
        ("immersion_kwh", 62191, "Elpatron energi"),
    ):
        start = f'ModbusSensor("{key}", {register},'
        line = next(line for line in const.splitlines() if start in line)
        assert f'"{name}"' in line and '"kWh"' in line and '"total_increasing"' in line, key
        assert f"**{name}**, register {register}" in section, name
    assert "`total_increasing`" in section
    assert "*Avgiven värme totalt*" in section and "not electricity it has used" in section


def test_the_removal_names_what_stays_behind():
    removal = _flat(_section("Removing the integration"))
    card = (COMPONENT / "card.py").read_text(encoding="utf-8")
    assert 'CARD_URL = f"/{DOMAIN}/ctc-ecozenith-card.js"' in card
    assert "`/ctc_ecozenith/ctc-ecozenith-card.js`" in removal, "resursen som blir kvar"
    assert "*Send anonymous usage statistics* off" in removal, "radering av statistiken"
    assert "config/custom_components/ctc_ecozenith" in removal


def test_the_licence_names_its_holder():
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert "[yyyy]" not in licence and "[name of copyright owner]" not in licence
    assert "   Copyright 2026 Andreas Reimers\n" in licence
    assert "[Apache License 2.0](LICENSE)" in _section("Licence")
