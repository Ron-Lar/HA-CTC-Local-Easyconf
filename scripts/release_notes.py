#!/usr/bin/env python3
"""Write the notes of a release from its tag, for .github/workflows/release.yml (R73).

Usage: python3 scripts/release_notes.py vX.Y.Z > notes.md

The notes are the annotated tag's message, without a signature, when it says
more than one line. A lightweight tag, or a message of one line such as
"CTC EcoZenith 0.9.3", gets the subjects of the commits since the tag before
it instead, under that line where there is one.

The tag must be in the local repository as the tag object: actions/checkout
leaves a pushed tag pointing at its commit, so the workflow fetches the tag
again first. Only the standard library and git.
"""

from __future__ import annotations

import re
import subprocess
import sys

#: What a release tag looks like here; anything else is refused before git sees it.
TAG = re.compile(r"v\d+\.\d+\.\d+")
SIGNATURE = re.compile(r"^-----BEGIN [A-Z ]*SIGNATURE-----$", re.MULTILINE)


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def tag_message(tag: str) -> str:
    """The annotated tag's own message, or nothing for a lightweight tag."""
    if _git("cat-file", "-t", f"refs/tags/{tag}").strip() != "tag":
        return ""
    # A tag object is a header, a blank line, then the message.
    message = _git("cat-file", "tag", f"refs/tags/{tag}").partition("\n\n")[2]
    return SIGNATURE.split(message, maxsplit=1)[0].strip()


def previous_tag(tag: str) -> str | None:
    """The nearest release tag before this one in its history, if any."""
    try:
        found = _git("describe", "--tags", "--abbrev=0", "--match", "v*", f"refs/tags/{tag}^")
    except subprocess.CalledProcessError:
        return None
    return found.strip() or None


def notes(tag: str) -> str:
    if not TAG.fullmatch(tag):
        raise ValueError(f"not a release tag: {tag!r}")
    message = tag_message(tag)
    if len([line for line in message.splitlines() if line.strip()]) > 1:
        return f"{message}\n"
    previous = previous_tag(tag)
    since = f"refs/tags/{previous}..refs/tags/{tag}" if previous else f"refs/tags/{tag}"
    log = _git("log", "--no-merges", "--format=- %s", since).strip()
    heading = f"Changes since {previous}:" if previous else "Changes:"
    title = f"{message}\n\n" if message else ""
    return f"{title}{heading}\n\n{log}\n"


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    sys.stdout.write(notes(argv[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
