"""The quality scale checklist against the Home Assistant release beside it (R56).

tests/test_quality_scale.py holds quality_scale.yaml to the rules of the scale
as Home Assistant 2026.9 has them, read without a YAML library. Here the file
is read with Home Assistant's own loader, the reading has to agree with that
one, and every rule the installed release knows needs a row: hassfest
requires every core integration to list every rule, so the core files of the
release say which rules there are. A release that adds a rule fails this test
until the rule has a row of its own, in both files. An older release knows
fewer (2026.5 has neither docs-conditions nor docs-triggers), and the rows it
does not know are fine as long as they are rules of the scale as
tests/test_quality_scale.py lists it.

Runs from a virtual environment with Home Assistant and
pytest-homeassistant-custom-component installed, like the other
test_homeassistant files, and skips itself elsewhere.
"""

from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from conftest import COMPONENT
import homeassistant
from homeassistant.util.yaml import load_yaml_dict
from test_quality_scale import RULES, SCALE
import voluptuous as vol

CORE = pathlib.Path(homeassistant.__file__).resolve().parent / "components"


def _release_rules() -> set[str]:
    files = sorted(CORE.glob("*/quality_scale.yaml"))
    assert files, "inga quality_scale.yaml i kärnan"
    rules: set[str] = set()
    for path in files:
        rules |= set(load_yaml_dict(str(path)).get("rules", {}))
    return rules


def _ours() -> dict:
    return load_yaml_dict(str(COMPONENT / "quality_scale.yaml"))


def test_every_rule_the_installed_release_knows_has_a_row():
    known = _release_rules()
    assert known <= set(SCALE), f"saknas: {sorted(known - set(SCALE))}"
    assert known <= set(RULES), f"saknas i RULES: {sorted(known - set(RULES))}"
    assert set(SCALE) <= known | set(RULES), f"okända: {sorted(set(SCALE) - known - set(RULES))}"


def test_home_assistants_reading_of_the_file_is_the_suites():
    rules = _ours()["rules"]
    assert {name: dict(row) for name, row in rules.items()} == SCALE


def test_the_file_passes_hassfests_schema_for_a_core_integration():
    # hassfest's SCHEMA in script/hassfest/quality_scale.py, built from the
    # rules of the installed release and the suite's list: todo and done may
    # stand alone or with a comment, exempt needs one.
    plain = vol.In(["todo", "done"])
    row = vol.Any(
        plain,
        vol.Schema({vol.Required("status"): plain, vol.Required("comment"): str}),
        vol.Schema({vol.Required("status"): "exempt", vol.Required("comment"): str}),
    )
    rules = _release_rules() | set(RULES)
    schema = vol.Schema(
        {vol.Required("rules"): vol.Schema({vol.Required(name): row for name in rules})}
    )
    schema(_ours())
