"""The weekly run may go red over a new pymodbus major; a push may not (R59).

The "latest" leg of the test matrix installs whatever PyPI has, past the
manifest's own cap, so that a new major is met in CI before a user meets it.
A job that is allowed to fail leaves the run green, and GitHub notifies nobody
about a green run, so the allowance must not cover the scheduled run: a red
Monday is the whole point of the leg. A push or a pull request, on the other
hand, is never blocked by a version the manifest already keeps out of every
installation.
"""

from __future__ import annotations

import pathlib
import re
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yaml"


def _text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def _latest_leg_allowance() -> str:
    """The continue-on-error expression of the job that has the pymodbus matrix."""
    lines = [
        line.strip()
        for line in _text().splitlines()
        if line.strip().startswith("continue-on-error:") and "matrix.pymodbus" in line
    ]
    assert len(lines) == 1, lines
    return lines[0].split(":", 1)[1].strip()


def _allowed_to_fail(expression: str, *, leg: str, event: str) -> bool:
    """Evaluate the workflow expression the way GitHub would, for one job."""
    assert expression.startswith("${{") and expression.endswith("}}"), expression
    code = expression[3:-2].replace("&&", " and ").replace("||", " or ")
    # Only names, quoted strings, comparisons and parentheses: nothing to run.
    assert re.fullmatch(r"[\w.'=!()\s]+", code), code
    scope = {
        "matrix": SimpleNamespace(pymodbus=leg),
        "github": SimpleNamespace(event_name=event),
    }
    return bool(eval(code, {"__builtins__": {}}, scope))


@pytest.mark.parametrize("event", ["push", "pull_request"])
def test_the_latest_leg_never_blocks_a_push_or_a_pull_request(event):
    assert _allowed_to_fail(_latest_leg_allowance(), leg="latest", event=event)


@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_the_latest_leg_fails_the_scheduled_run_so_somebody_is_told(event):
    """A run started by hand is somebody asking whether all is well now (R57)."""
    assert not _allowed_to_fail(_latest_leg_allowance(), leg="latest", event=event)


@pytest.mark.parametrize("event", ["push", "pull_request", "schedule", "workflow_dispatch"])
def test_the_pinned_legs_are_never_allowed_to_fail(event):
    allowance = _latest_leg_allowance()
    for leg in ("3.6.9", "3.13.1"):
        assert not _allowed_to_fail(allowance, leg=leg, event=event), (leg, event)


def test_the_weekly_run_exists_and_the_latest_leg_takes_whatever_pypi_has():
    text = _text()
    assert re.search(r"^\s+- cron: ", text, re.MULTILINE)
    assert "pip install --upgrade pymodbus" in text
    assert re.search(r'pymodbus: \[.*"latest"\]', text)


def _job(name: str) -> str:
    """The lines of one job, from its key to the next job's."""
    lines = _text().splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i])), len(lines)
    )
    return "\n".join(lines[start:end])


def test_the_hacs_job_blocks_every_run():
    """Public since 2026-09-10, so HACS can read the repository and its verdict counts (R57)."""
    job = _job("hacs")
    assert "hacs/action@" in job
    assert "continue-on-error" not in job
    assert not re.search(r"^    if:", job, re.MULTILINE)


def test_pushes_run_on_main_only_and_a_run_can_be_started_by_hand():
    text = _text()
    assert re.search(r"^  push:\n    branches: \[main\]$", text, re.MULTILINE)
    assert re.search(r"^  workflow_dispatch:$", text, re.MULTILINE)


def test_ruff_runs_in_a_pinned_version():
    """A new ruff release brings new rules; CI goes red over this repository, not over ruff."""
    job = _job("ruff")
    assert re.search(r'^\s+RUFF_VERSION: "\d+\.\d+\.\d+"$', job, re.MULTILINE)
    assert 'pip install "ruff==${RUFF_VERSION}"' in job
