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

So the floor is 2026.5.0, the highest need in NEEDS. Each need is found by its
name in the package's syntax tree, not by a piece of text, so a rewrite that
spells it another way (imported by name, kept in a variable, moved to another
file) still finds it. Lowering hacs.json below a need fails here, and so does a
need that is no longer found: it comes out of NEEDS only once
scripts/smoke_real_ha.py under the release before it has confirmed that the
code no longer uses it, never because the floor would then go down by itself.
That smoke run, against the floor in CI, finds on its own a Home Assistant
module, function, class or attribute newer than the floor; a method on an
object or an argument to one it does not see, and code that starts using one
adds its line here by hand. Home Assistant from 2026.3 on needs Python 3.14.2
or later, which is also what a floor run in CI has to use.
"""

from __future__ import annotations

import ast
import json
import re

import pytest

from conftest import COMPONENT, ROOT

#: (the Home Assistant module or name the source uses, the first release that has it)
NEEDS = (
    ("homeassistant.helpers.service_info.dhcp", "2025.2.0"),
    ("homeassistant.components.frontend.async_panel_exists", "2026.5.0"),
)


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def _hacs() -> dict:
    return json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))


def _within(dotted: str, need: str) -> bool:
    return dotted == need or dotted.startswith(f"{need}.")


def _lines_using(source: str, need: str) -> list[int]:
    """The lines where the source reaches a Home Assistant module or name.

    Through an import of it or of something in it (from M import n, from M.n
    import x, import M.n), or through an attribute chain that starts at a name
    an import bound (frontend.async_panel_exists, also when kept in a variable).
    A comment, a string or getattr with a name in quotes is not a use.
    """
    tree = ast.parse(source)
    bound: dict[str, str] = {}
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                full = f"{node.module}.{alias.name}"
                bound[alias.asname or alias.name] = full
                if _within(full, need) or _within(node.module, need):
                    lines.append(node.lineno)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.partition(".")[0]
                bound[alias.asname or top] = alias.name if alias.asname else top
                if _within(alias.name, need):
                    lines.append(node.lineno)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        parts = []
        root: ast.expr = node
        while isinstance(root, ast.Attribute):
            parts.append(root.attr)
            root = root.value
        if (
            isinstance(root, ast.Name)
            and root.id in bound
            and _within(".".join([bound[root.id], *reversed(parts)]), need)
        ):
            lines.append(node.lineno)
    return sorted(set(lines))


def _uses(need: str) -> list[str]:
    """Where in the package the need is used, as file:line."""
    return [
        f"{path.relative_to(COMPONENT).as_posix()}:{line}"
        for path in sorted(COMPONENT.rglob("*.py"))
        for line in _lines_using(path.read_text(encoding="utf-8"), need)
    ]


def test_the_floor_is_a_first_release_of_a_month():
    assert re.fullmatch(r"20\d\d\.(?:[1-9]|1[0-2])\.0", _hacs()["homeassistant"])


@pytest.mark.parametrize(("need", "release"), NEEDS)
def test_every_need_is_still_used_by_the_source(need, release):
    assert _uses(need), (
        f"{need} ({release}) hittas inte längre i källan. Bekräfta med proben, "
        f"scripts/smoke_real_ha.py under Home Assistant före {release}, att kravet "
        "är borta, och ta sedan bort raden ur NEEDS."
    )


@pytest.mark.parametrize(("need", "release"), NEEDS)
def test_the_floor_is_no_lower_than_what_the_source_uses(need, release):
    floor = _hacs()["homeassistant"]
    assert _version(floor) >= _version(release), (
        f"{', '.join(_uses(need))} använder {need}, som kom i {release}, "
        f"men hacs.json lovar {floor}"
    )


def test_the_floor_is_the_highest_need_and_not_above_it():
    floor = _hacs()["homeassistant"]
    highest = max(NEEDS, key=lambda need: _version(need[1]))
    assert _version(floor) <= _version(highest[1]), (
        f"hacs.json lovar {floor}, men det högsta kravet i NEEDS är {highest[0]} "
        f"({highest[1]}): det som höjde golvet ska stå i NEEDS"
    )
    assert _version(floor) == _version(highest[1]), "golvet ska vara det högsta kravet"


@pytest.mark.parametrize(
    ("source", "need", "used"),
    [
        (
            "from homeassistant.components import frontend\n"
            "frontend.async_panel_exists(hass, 'ctc')",
            "homeassistant.components.frontend.async_panel_exists",
            [2],
        ),
        (
            "from homeassistant.components import frontend\n"
            "exists = frontend.async_panel_exists\n",
            "homeassistant.components.frontend.async_panel_exists",
            [2],
        ),
        (
            "def register(hass):\n"
            "    from homeassistant.components.frontend import async_panel_exists\n",
            "homeassistant.components.frontend.async_panel_exists",
            [2],
        ),
        (
            "import homeassistant.components.frontend as fe\nfe.async_panel_exists\n",
            "homeassistant.components.frontend.async_panel_exists",
            [2],
        ),
        (
            "from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo\n",
            "homeassistant.helpers.service_info.dhcp",
            [1],
        ),
        (
            "from homeassistant.helpers.service_info import dhcp\n",
            "homeassistant.helpers.service_info.dhcp",
            [1],
        ),
        (
            "from homeassistant.helpers import service_info\n"
            "service_info.dhcp.DhcpServiceInfo\n",
            "homeassistant.helpers.service_info.dhcp",
            [2],
        ),
        (
            "from homeassistant.components import frontend\n"
            "# frontend.async_panel_exists(hass, 'ctc')\n"
            "text = 'frontend.async_panel_exists('\n"
            "getattr(frontend, 'async_panel_exists', None)\n"
            "frontend.async_register_built_in_panel\n",
            "homeassistant.components.frontend.async_panel_exists",
            [],
        ),
        (
            "from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo\n",
            "homeassistant.helpers.service_info.dhcp",
            [],
        ),
    ],
)
def test_a_need_is_found_by_its_name_however_it_is_spelt(source, need, used):
    assert _lines_using(source, need) == used


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
