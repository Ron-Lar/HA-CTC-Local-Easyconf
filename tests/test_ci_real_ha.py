"""CI meets a real Home Assistant three ways, and the floor is read from hacs.json (R58).

scripts/smoke_real_ha.py needs Home Assistant installed and runs in CI only;
what is checked here is the wiring around it: which release each job takes,
that the floor follows hacs.json without a second copy of the version in the
workflow, that the weekly job is the strict one, the helper that names a
release and the Python it was built for, and that every way of running the
smoke resolves every Home Assistant name the source spells, following only
the names an import binds.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
import re
import sys
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yaml"
SMOKE = ROOT / "scripts" / "smoke_real_ha.py"


def _job(name: str) -> str:
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i])), len(lines)
    )
    return "\n".join(lines[start:end])


@pytest.fixture(scope="module")
def ha_release():
    spec = importlib.util.spec_from_file_location(
        "ha_release", ROOT / "scripts" / "ha_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def smoke():
    """The smoke script as a module; it imports Home Assistant only inside its steps."""
    path = list(sys.path)
    spec = importlib.util.spec_from_file_location("smoke_real_ha", SMOKE)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    finally:
        # The script puts the repository first on the path for itself.
        sys.path[:] = path
    return module


def test_the_floor_is_whatever_hacs_json_says(ha_release):
    hacs = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
    assert ha_release.floor() == hacs["homeassistant"]
    assert ha_release.RELEASE.fullmatch(ha_release.floor())


def test_the_floor_job_reads_the_file_and_names_no_version_of_its_own():
    job = _job("smoke-floor")
    assert "scripts/ha_release.py floor" in job
    assert "steps.release.outputs.python" in job
    assert not re.search(r"\d{4}\.\d{1,2}\.\d+", job), job
    assert re.search(r"^\s+- run: python scripts/smoke_real_ha.py$", job, re.MULTILINE)


def test_the_newest_release_is_met_weekly_and_by_hand_and_strictly():
    job = _job("smoke-latest")
    assert "scripts/ha_release.py latest" in job
    assert "scripts/smoke_real_ha.py --strict" in job
    assert "github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'" in job


def test_pushes_and_pull_requests_meet_the_houses_release_on_3_14():
    job = _job("smoke-house")
    assert re.search(r'HA_VERSION: "\d{4}\.\d{1,2}\.\d+"', job)
    assert 'python-version: "3.14"' in job
    assert "github.event_name == 'push' || github.event_name == 'pull_request'" in job


def test_the_home_assistant_tests_cannot_pass_by_skipping_themselves():
    job = _job("ha-tests")
    assert re.search(r'PLUGIN_VERSION: "\d+\.\d+\.\d+"', job)
    assert "shell: bash" in job  # pipefail, so a failing pytest is not hidden by tee
    assert "asyncio_mode=auto" in job
    assert "could not import 'pytest_homeassistant_custom_component'" in job


def test_the_python_is_the_newest_the_release_names(ha_release):
    classifiers = [
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3 :: Only",
        "Topic :: Home Automation",
    ]
    assert ha_release.newest_python(classifiers) == "3.13"
    assert ha_release.newest_python(["Programming Language :: Python :: 3.14"]) == "3.14"
    with pytest.raises(ValueError):
        ha_release.newest_python(["Topic :: Home Automation"])


@pytest.mark.parametrize("spec", ["2026.11.0b0", "2026.9", "latest; rm -rf /", ""])
def test_anything_but_a_release_is_refused_before_pypi_is_asked(ha_release, spec, monkeypatch):
    def no_network(_version):
        raise AssertionError("asked PyPI")

    monkeypatch.setattr(ha_release, "_pypi", no_network)
    with pytest.raises(ValueError):
        ha_release.resolve(spec)


def test_every_way_of_running_the_smoke_resolves_the_names_right_after_the_imports():
    tree = ast.parse(SMOKE.read_text(encoding="utf-8"))
    run = next(
        node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run"
    )
    blocks = [
        block
        for node in ast.walk(run)
        for block in (getattr(node, "body", None), getattr(node, "orelse", None))
        if isinstance(block, list)
    ]
    following = [
        ast.unparse(block[index + 1])
        for block in blocks
        for index, statement in enumerate(block[:-1])
        if ast.unparse(statement) == "imported = _import_everything(report)"
    ]
    assert following == ["_statistics(hass, report, _names(report))"]
    # Behind no if: it runs with and without --strict, whatever imported.
    for node in ast.walk(run):
        if isinstance(node, ast.If):
            assert "_names" not in ast.unparse(node), ast.unparse(node.test)


def test_the_names_step_follows_only_what_an_import_binds(smoke):
    source = """
from homeassistant.helpers import entity_registry as er
import voluptuous as vol


def setup(hass, entry):
    from homeassistant.components.recorder import get_instance
    try:
        from homeassistant.helpers.start import async_at_started
    except Exception:
        return
    er.async_get(hass)
    async_at_started(hass, get_instance)
    entry.get("x")
    vol.Schema({})
    settings = {}
    settings.pop("x")


def other(er):
    return er.async_get


class Flow:
    selector = None

    def step(self):
        from homeassistant.helpers import selector

        return selector.SelectSelectorMode.LIST, [er.x for er in ()]
"""
    imports, chains, calls = smoke._scan(ast.parse(source))
    assert sorted(f"{statement.module}.{alias.name}" for statement, alias in imports) == [
        "homeassistant.components.recorder.get_instance",
        "homeassistant.helpers.entity_registry",
        "homeassistant.helpers.selector",
        "homeassistant.helpers.start.async_at_started",
    ]
    followed = sorted(
        ast.unparse(chain)
        for chain, scope in chains
        if isinstance(root := chain.value, ast.Attribute | ast.Name)
        and scope.lookup(ast.unparse(root).partition(".")[0]) is not None
    )
    assert followed == ["er.async_get", "selector.SelectSelectorMode.LIST"]
    assert [
        ast.unparse(call.func)
        for call, scope in calls
        if isinstance(call.func, ast.Name) and scope.lookup(call.func.id) is not None
    ] == ["async_at_started"]


def test_a_call_is_held_to_the_real_signature_by_its_arguments(smoke):
    def register(hass, path, *, require_admin=False):
        """A stand-in with the shape of a Home Assistant helper."""

    def call(text: str) -> ast.Call:
        return ast.parse(text).body[0].value

    assert smoke._fits(register, None, call("register(hass, 'ctc', require_admin=True)"))
    with pytest.raises(TypeError, match="admin_only"):
        smoke._fits(register, None, call("register(hass, 'ctc', admin_only=True)"))
    with pytest.raises(TypeError, match="positional"):
        smoke._fits(register, None, call("register(hass, 'ctc', True)"))
    # A method reached through its class and a dict built by keywords are not held.
    assert not smoke._fits(register, type("Panel", (), {}), call("Panel.register(hass)"))
    assert not smoke._fits(dict, None, call("dict(anything=1)"))


def test_a_name_is_reached_for_as_from_its_own_file_and_line(smoke):
    """Home Assistant's notice of something on its way out names the file of the
    frame that reached for it, and --strict reads that notice."""
    path = smoke.COMPONENT / "seen_history.py"

    class Module:
        def __getattr__(self, name):
            warnings.warn(f"{name} is on its way out", DeprecationWarning, stacklevel=2)
            return name

    node = ast.parse("\n" * 40 + "frontend.old_name", mode="eval").body
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        assert smoke._as_written(path, node, {"frontend": Module()}) == "old_name"
    assert [(w.filename, w.lineno) for w in caught] == [(str(path), 41)]
    namespace = smoke._as_written(path, ast.parse("import json").body[0])
    assert namespace["__name__"] == "custom_components.ctc_ecozenith.seen_history"
    assert namespace["json"] is json
