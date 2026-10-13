#!/usr/bin/env python3
"""Name a Home Assistant release and the Python it was built for, for CI.

Usage: python3 scripts/ha_release.py floor|latest|<version>

``floor`` is the release hacs.json promises, read from the file so that raising
the floor is a change to hacs.json alone; ``latest`` is the newest release on
PyPI. Prints ``version=...`` and ``python=...`` lines, ready for
$GITHUB_OUTPUT. The Python is the newest the release's own classifiers name,
the one Home Assistant OS and the container image shipped it with: 3.13 for
2025.1, 3.14 since 2026.3.

Only the standard library, so it runs on the runner's own python3 before any
Python is set up.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PYPI = "https://pypi.org/pypi/homeassistant/{}json"
#: A release as Home Assistant numbers them; a beta or anything else is refused.
RELEASE = re.compile(r"\d{4}\.\d{1,2}\.\d+")
CLASSIFIER = re.compile(r"Programming Language :: Python :: (3)\.(\d+)")


def floor() -> str:
    """The release hacs.json promises the integration runs on."""
    return str(json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))["homeassistant"])


def newest_python(classifiers: list[str]) -> str:
    """The newest Python version a list of trove classifiers names."""
    found = [
        (int(match[1]), int(match[2]))
        for item in classifiers
        if (match := CLASSIFIER.fullmatch(item))
    ]
    if not found:
        raise ValueError("the release names no Python 3 version among its classifiers")
    major, minor = max(found)
    return f"{major}.{minor}"


def _pypi(version: str | None) -> dict:
    url = PYPI.format(f"{version}/" if version else "")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                return json.load(response)["info"]
        except OSError:
            if attempt == 2:
                raise
            time.sleep(5 * (attempt + 1))
    raise AssertionError("unreachable")


def resolve(spec: str) -> tuple[str, str]:
    """The release ``spec`` names and the Python to run it on."""
    wanted = floor() if spec == "floor" else None if spec == "latest" else spec
    if wanted is not None and not RELEASE.fullmatch(wanted):
        raise ValueError(f"not a Home Assistant release: {wanted!r}")
    info = _pypi(wanted)
    version = str(info["version"])
    if not RELEASE.fullmatch(version):
        raise ValueError(f"PyPI answered with something that is not a release: {version!r}")
    return version, newest_python(list(info.get("classifiers") or []))


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.splitlines()[2], file=sys.stderr)
        return 2
    version, python = resolve(argv[0])
    print(f"version={version}")
    print(f"python={python}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
