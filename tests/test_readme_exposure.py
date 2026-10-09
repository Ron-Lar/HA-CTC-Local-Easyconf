"""The README says what the integration needs opened, and how to fence it in (R71).

The integration needs the display's web interface on port 80 and Modbus TCP on
port 502, and neither has a login: anyone who reaches the first can press
anywhere on the panel, the installer menus included, and anyone who reaches
the second can write registers. The README asked for Modbus TCP without a word
about either. This holds the section that says it to its points, so a later
edit of the README cannot drop one unnoticed.
"""

from __future__ import annotations

from conftest import ROOT


def _section() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Network and exposure" in readme
    return readme.split("## Network and exposure")[1].split("\n## ")[0]


def test_both_ports_and_what_switches_them_on():
    section = _section()
    assert "port 80" in section and "port 502" in section
    assert "**Web** to **Yes**" in section, "hur Web slås på"
    assert "**Modbus TCP**" in section


def test_it_says_there_is_no_login_and_that_the_integration_cannot_add_one():
    section = _section()
    assert "no login" in section and "no authentication" in section
    assert "cannot add a login" in section
    assert "Service" in section and "Define" in section, "vad en främling når i panelen"


def test_it_gives_the_advice_and_the_one_rule():
    section = _section()
    assert "VLAN" in section and "firewall rule" in section
    assert "only Home Assistant" in section
    assert "Never open port 80 or 502 to the internet" in section


def test_setup_points_to_it():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    setup = readme.split("## Setup")[1].split("\n## ")[0]
    assert "(#network-and-exposure)" in setup
