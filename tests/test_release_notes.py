"""The release's notes come from its tag, else from the commits since the tag before (R73).

The release workflow can only be seen working once a tag is pushed, so the
notes are written by scripts/release_notes.py, and here it is run against a
repository made for the test: a lightweight tag, an annotated one whose
message is one line, one with real notes and one with a signature under them.
"""

from __future__ import annotations

import importlib.util
import pathlib
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")


@pytest.fixture(scope="module")
def release_notes():
    spec = importlib.util.spec_from_file_location(
        "release_notes", ROOT / "scripts" / "release_notes.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """An empty repository, out of reach of the user's own git configuration."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.chdir(tmp_path)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid", *args],
            check=True, capture_output=True, text=True,
        )

    git("init", "-q", "-b", "main")
    return git


def _commit(git, subject: str) -> None:
    git("commit", "-q", "--allow-empty", "-m", subject)


def test_a_lightweight_tag_gets_the_commits_since_the_tag_before(release_notes, repo):
    _commit(repo, "First (0.1.0)")
    repo("tag", "v0.1.0")
    _commit(repo, "A sensor no longer stops at an unknown status (0.1.1)")
    _commit(repo, "The page says why (0.2.0)")
    repo("tag", "v0.2.0")
    assert release_notes.notes("v0.2.0") == (
        "Changes since v0.1.0:\n\n"
        "- The page says why (0.2.0)\n"
        "- A sensor no longer stops at an unknown status (0.1.1)\n"
    )


def test_a_one_line_message_heads_the_commits_rather_than_standing_for_the_notes(
    release_notes, repo
):
    _commit(repo, "First (0.1.0)")
    repo("tag", "-a", "v0.1.0", "-m", "CTC EcoZenith 0.1.0")
    _commit(repo, "Second (0.1.1)")
    repo("tag", "-a", "v0.1.1", "-m", "CTC Local Easyconf 0.1.1")
    assert release_notes.notes("v0.1.1") == (
        "CTC Local Easyconf 0.1.1\n\nChanges since v0.1.0:\n\n- Second (0.1.1)\n"
    )
    assert release_notes.notes("v0.1.0") == "CTC EcoZenith 0.1.0\n\nChanges:\n\n- First (0.1.0)\n"


def test_an_annotated_tag_with_notes_gives_them_as_written_without_a_signature(
    release_notes, repo
):
    _commit(repo, "First (0.1.0)")
    written = "A fix for the sensors.\n\n- Status 7 no longer stops the sensor\n- The page says why"
    signature = "-----BEGIN PGP SIGNATURE-----\n\niQEzBAABCAAdFiEE\n-----END PGP SIGNATURE-----"
    repo("tag", "-a", "v0.1.0", "-m", written)
    assert release_notes.notes("v0.1.0") == f"{written}\n"
    _commit(repo, "Second (0.1.1)")
    repo("tag", "-a", "v0.1.1", "-m", f"{written}\n{signature}")
    assert release_notes.notes("v0.1.1") == f"{written}\n"


@pytest.mark.parametrize("tag", ["0.1.0", "v0.1", "v0.1.0; ls", "--help", "v0.1.0^"])
def test_anything_but_a_release_tag_is_refused(release_notes, tag):
    with pytest.raises(ValueError):
        release_notes.notes(tag)
