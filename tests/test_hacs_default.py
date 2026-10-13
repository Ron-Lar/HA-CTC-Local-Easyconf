"""What HACS checks before the integration can join its default list (R75).

The pull request to hacs/default runs the HACS action against the repository
without any ignore, hassfest, and its own checks: a release, an owner, a
description, issues and topics. Most of that lives on GitHub. What lives in
the repository is held here, so that the release the request points at cannot
fail on it: hacs.json with only the keys HACS's own schema lets through (it
refuses any other), the manifest with the keys and links HACS requires of an
integration, one integration under custom_components, the brand icon HACS's
brands check looks for in the integration's own brand directory, a licence
and a README.
"""

from __future__ import annotations

import json
import re
import struct

from conftest import COMPONENT, ROOT

#: HACS_MANIFEST_JSON_SCHEMA in hacs/integration (custom_components/hacs/utils/
#: validate.py): the keys of hacs.json and their types, with extra keys refused.
HACS_JSON = {
    "name": str,
    "content_in_root": bool,
    "country": (str, list),
    "filename": str,
    "hacs": str,
    "hide_default_branch": bool,
    "homeassistant": str,
    "persistent_directory": str,
    "render_readme": bool,
    "zip_release": bool,
}

#: The keys of manifest.json HACS requires of an integration (the same file's
#: INTEGRATION_MANIFEST_JSON_SCHEMA).
MANIFEST_KEYS = ("codeowners", "documentation", "domain", "issue_tracker", "name", "version")


def _hacs() -> dict:
    return json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))


def _manifest() -> dict:
    return json.loads((COMPONENT / "manifest.json").read_text(encoding="utf-8"))


def test_hacs_json_has_only_keys_hacs_lets_through_with_their_types():
    hacs = _hacs()
    assert set(hacs) <= set(HACS_JSON), set(hacs) - set(HACS_JSON)
    for key, value in hacs.items():
        assert isinstance(value, HACS_JSON[key]), key


def test_hacs_json_is_named_as_the_manifest_and_needs_no_zip():
    hacs = _hacs()
    assert hacs["name"] == _manifest()["name"]
    # HACS takes the integration's folder from the release's tree.
    assert not hacs.get("zip_release") and not hacs.get("content_in_root")
    # A heat pump sold well beyond one country: the listing is not limited.
    assert "country" not in hacs


def test_the_manifest_has_what_hacs_requires_and_links_to_this_repository():
    manifest = _manifest()
    for key in MANIFEST_KEYS:
        assert manifest.get(key), key
    assert manifest["domain"] == COMPONENT.name
    assert isinstance(manifest["codeowners"], list)
    repository = manifest["documentation"]
    assert re.fullmatch(r"https://github\.com/[\w.-]+/[\w.-]+", repository), repository
    assert manifest["issue_tracker"] == f"{repository}/issues"
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])


def test_one_integration_under_custom_components():
    folders = [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir()]
    folders = [name for name in folders if not name.startswith((".", "__"))]
    assert folders == [COMPONENT.name]


def _png_size(name: str) -> tuple[int, int]:
    head = (COMPONENT / "brand" / name).read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR", name
    return struct.unpack(">II", head[16:24])


def test_the_brand_icon_hacs_looks_for_is_there_and_square():
    # Home Assistant's own sizes for an integration's icon: 256 and, for
    # high resolution screens, 512 pixels square.
    assert _png_size("icon.png") == (256, 256)
    assert _png_size("icon@2x.png") == (512, 512)


def test_a_licence_and_a_readme():
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")
    assert (ROOT / "README.md").read_text(encoding="utf-8").startswith("# ")
