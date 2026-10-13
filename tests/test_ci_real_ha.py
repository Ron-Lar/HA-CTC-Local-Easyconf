"""CI meets a real Home Assistant three ways, and the floor is read from hacs.json (R58).

scripts/smoke_real_ha.py needs Home Assistant installed and runs in CI only;
what is checked here is the wiring around it: which release each job takes,
that the floor follows hacs.json without a second copy of the version in the
workflow, that the weekly job is the strict one, and the helper that names a
release and the Python it was built for.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yaml"


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
