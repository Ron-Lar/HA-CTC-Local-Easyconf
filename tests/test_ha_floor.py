"""hacs.json promises a Home Assistant the code actually runs on (roadmap R56).

The floor was found by trying, not by reading: every module imported, every
Home Assistant name the source spells resolved (the ones imported inside
functions too), the calls held against the real signatures, the flows' forms
built and serialised, the page class built, the actions and the websocket
command registered, under Home Assistant 2024.12.5, 2025.1.4, 2025.2.0,
2026.4.0 and 2026.5.0. Two things set it:

* config_flow.py imports DhcpServiceInfo from
  homeassistant.helpers.service_info.dhcp, which Home Assistant 2025.2 brought.
  Before it the config flow does not import and nothing can be set up.
* dashboard.py asks frontend.async_panel_exists whether the page's address is
  taken, which Home Assistant 2026.5 brought. Before it the call raises, set-up
  catches it, and the CTC page is never added to the sidebar.

So the floor is 2026.5.0. Each entry below names what in the source needs which
release; lowering hacs.json below what the source still uses fails here, and a
change that drops the need (a fallback for async_panel_exists, say) takes its
entry out and may lower the floor with it. Home Assistant from 2026.3 on needs
Python 3.14.2 or later, which is also what a floor run in CI has to use.
"""

from __future__ import annotations

import ast
import json
import re

from conftest import COMPONENT, ROOT

#: (file, text in it, the first Home Assistant release that has it)
NEEDS = (
    ("config_flow.py", "from homeassistant.helpers.service_info.dhcp import", "2025.2.0"),
    ("dashboard.py", "frontend.async_panel_exists(", "2026.5.0"),
)

def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _hacs() -> dict:
    return json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))


def test_the_floor_is_a_first_release_of_a_month():
    assert re.fullmatch(r"20\d\d\.(?:[1-9]|1[0-2])\.0", _hacs()["homeassistant"])


def test_the_floor_is_no_lower_than_what_the_source_uses():
    floor = _version(_hacs()["homeassistant"])
    for name, text, release in NEEDS:
        if text in (COMPONENT / name).read_text(encoding="utf-8"):
            assert floor >= _version(release), f"{name} använder {text!r} som kom i {release}"


def test_the_floor_is_the_highest_need_and_not_above_it():
    used = [
        _version(release)
        for name, text, release in NEEDS
        if text in (COMPONENT / name).read_text(encoding="utf-8")
    ]
    assert _version(_hacs()["homeassistant"]) == max(used), "golvet ska vara det högsta kravet"


def test_the_readme_states_the_same_floor():
    year, month, _ = _hacs()["homeassistant"].split(".")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"Home Assistant {year}.{month} or later" in readme


def test_every_flow_step_returns_a_config_flow_result():
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    assert "from homeassistant.config_entries import ConfigFlowResult" in source
    assert "data_entry_flow" not in source and not re.search(r"(?<!Config)FlowResult\b", source)
    tree = ast.parse(source)
    steps = [
        node
        for cls in tree.body
        if isinstance(cls, ast.ClassDef)
        for node in cls.body
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef)
        and (node.name.startswith("async_step_") or node.name.endswith("_form"))
    ]
    assert len(steps) > 10
    for step in steps:
        assert step.returns is not None, step.name
        assert ast.unparse(step.returns) == "ConfigFlowResult", step.name
