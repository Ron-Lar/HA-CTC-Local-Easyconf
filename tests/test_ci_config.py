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

import json
import pathlib
import re
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yaml"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"

#: The pymodbus each floor in hacs.json pins in its package_constraints.txt.
FLOOR_PYMODBUS = {"2026.5.0": "3.11.2"}

#: How a release is tagged and pushed, the same in release.yml and the README.
RELEASE_COMMAND = re.compile(
    r"git tag -a --cleanup=whitespace (v\d+\.\d+\.\d+) -F notes\.md"
    r" && git push --atomic origin main \1$",
    re.MULTILINE,
)


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


def _evaluate(expression: str, *, event: str, leg: str | None = None) -> bool:
    """Evaluate a workflow expression the way GitHub would, for one job."""
    assert expression.startswith("${{") and expression.endswith("}}"), expression
    code = expression[3:-2].replace("&&", " and ").replace("||", " or ")
    # Only names, quoted strings, comparisons and parentheses: nothing to run.
    assert re.fullmatch(r"[\w.'=!()\s]+", code), code
    scope = {
        "matrix": SimpleNamespace(pymodbus=leg),
        "github": SimpleNamespace(event_name=event),
    }
    return bool(eval(code, {"__builtins__": {}}, scope))


def _allowed_to_fail(expression: str, *, leg: str, event: str) -> bool:
    return _evaluate(expression, event=event, leg=leg)


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
    for leg in ("3.6.9", "3.11.2", "3.13.1"):
        assert not _allowed_to_fail(allowance, leg=leg, event=event), (leg, event)


def test_the_weekly_run_exists_and_the_latest_leg_takes_whatever_pypi_has():
    text = _text()
    assert re.search(r"^\s+- cron: ", text, re.MULTILINE)
    assert "pip install --upgrade pymodbus" in text
    assert re.search(r'pymodbus: \[.*"latest"\]', text)


def _job(name: str, workflow: pathlib.Path = WORKFLOW) -> str:
    """The lines of one job, from its key to the next job's."""
    lines = workflow.read_text(encoding="utf-8").splitlines()
    start = lines.index(f"  {name}:")
    end = next(
        (i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i])), len(lines)
    )
    return "\n".join(lines[start:end])


def _jobs(workflow: pathlib.Path = WORKFLOW) -> list[str]:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    after = lines[lines.index("jobs:") + 1 :]
    return [match[1] for line in after if (match := re.match(r"^  ([\w-]+):$", line))]


def _triggers(workflow: pathlib.Path) -> list[str]:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    after = lines[lines.index("on:") + 1 :]
    block = after[: next(i for i, line in enumerate(after) if not line.strip())]
    return [match[1] for line in block if (match := re.match(r"^  ([\w-]+):", line))]


def test_the_floor_s_pymodbus_is_a_pinned_leg_on_3_14():
    """Home Assistant installs pymodbus under its own constraints (F1.3, R60).

    So a user on the floor in hacs.json runs the pymodbus that release pins,
    whatever the manifest allows. Raising the floor means looking up the new
    release's pin in its package_constraints.txt and giving it this leg.
    """
    floor = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))["homeassistant"]
    assert floor in FLOOR_PYMODBUS, f"look up pymodbus in {floor}'s package_constraints.txt"
    pin = FLOOR_PYMODBUS[floor]
    included = re.split(r"^        include:$", _job("tests"), flags=re.MULTILINE)
    assert len(included) == 2, "the test matrix has no include"
    assert re.search(
        rf'^          - python: "3\.14"\n            pymodbus: "{re.escape(pin)}"$',
        included[1],
        re.MULTILINE,
    )
    for event in ("push", "pull_request", "schedule", "workflow_dispatch"):
        assert not _allowed_to_fail(_latest_leg_allowance(), leg=pin, event=event), event


def test_validate_can_be_called_and_the_release_waits_for_it():
    """A tag's release waits for all of validate.yaml on its commit (F2.1, R73, R75)."""
    assert re.search(r"^  workflow_call:$", _text(), re.MULTILINE)
    validate = _job("validate", RELEASE)
    assert re.search(r"^    uses: \./\.github/workflows/validate\.yaml$", validate, re.MULTILINE)
    # The called run keeps the read-only token release.yml gives every job.
    assert not re.search(r"^    permissions:", validate, re.MULTILINE)
    assert re.search(
        r"^permissions:\n  contents: read$", RELEASE.read_text(encoding="utf-8"), re.MULTILINE
    )
    assert re.search(r"^    needs: \[check, validate\]$", _job("release", RELEASE), re.MULTILINE)


def test_the_validation_a_tag_calls_runs_as_a_push_to_main():
    """A called workflow sees its caller's event, which for release.yml is always a push (F2.1).

    So the houses' release, the floor and the core suite all run, the latest
    legs may fail as on any push, and the newest release waits for Monday.
    """
    assert _triggers(RELEASE) == ["push"]
    runs = {}
    for name in _jobs():
        condition = re.search(r"^    if: (.+)$", _job(name), re.MULTILINE)
        runs[name] = condition is None or _evaluate(condition[1], event="push")
    gate = {"hassfest", "hacs", "ruff", "tests", "smoke-house", "smoke-floor", "ha-tests"}
    assert gate <= set(runs), set(runs)
    assert runs.pop("smoke-latest") is False
    assert all(runs.values()), runs
    assert _allowed_to_fail(_latest_leg_allowance(), leg="latest", event="push")


def test_a_tag_off_main_is_not_released():
    """The check fetches every branch and tests the checked-out commit against main (F2.2, R73).

    HEAD rather than GITHUB_SHA, so that an annotated tag cannot make the
    test name the tag object instead of its commit.
    """
    check = _job("check", RELEASE)
    assert re.search(r"^          fetch-depth: 0$", check, re.MULTILINE)
    step = next(line for line in check.splitlines() if "merge-base" in line)
    assert "git merge-base --is-ancestor HEAD origin/main ||" in step
    assert "GITHUB_SHA" not in step
    assert "::error::" in step and "exit 1" in step
    assert check.index("merge-base") < check.index("pytest -q")


@pytest.mark.parametrize("path", [RELEASE, ROOT / "README.md"], ids=["release.yml", "README"])
def test_the_release_command_keeps_headings_and_pushes_main_with_the_tag(path):
    """Headings survive the tag, and main goes up with it (F2.3, F2.2).

    git tag strips every line that starts with # unless told otherwise, and a
    push of the tag alone leaves main behind. Not --cleanup=verbatim: with a
    signed tag and a notes.md without a final newline, the signature would
    land in the notes.
    """
    assert RELEASE_COMMAND.search(path.read_text(encoding="utf-8")), path.name


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
