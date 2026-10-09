"""Every store the integration keeps stays at major version 1.

Home Assistant refuses to load a file whose major version is above the one
the code opens it with, and the release before opens each of these files at
version 1 with nothing to catch the refusal. A new shape is therefore a new
minor version, which older code reads straight through, keeps what it does
not know and writes back; never a new major, which would leave a return to
the release before with an entry that cannot be set up at all. Held here in
the source, which the ordinary suite can read without Home Assistant.
"""

from __future__ import annotations

import pathlib
import re

from conftest import load

COMPONENT = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "ctc_ecozenith"

#: The version constants the stores are opened with, and what each must be.
MAJORS = {
    "HARVEST_STORAGE_VERSION": 1,
    "ALARM_STORAGE_VERSION": 1,
    "SEEN_STORAGE_VERSION": 1,
}


def test_every_store_module_declares_major_one():
    for name in ("seen", "harvest", "alarms", "cop"):
        assert load(name).STORAGE_VERSION == 1, f"{name}.STORAGE_VERSION är inte 1"
    seen = load("seen")
    assert seen.STORAGE_MINOR_VERSION == 2, "den nya formen är minor 2"


def test_every_store_is_opened_at_major_one():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    opened = re.findall(r"\bStore\(\s*\n?\s*hass,\s*(\w+),", source)
    assert opened, "inga stores hittade i __init__.py"
    for version in opened:
        assert version == "1" or MAJORS.get(version) == 1, f"en store öppnas med {version}"


def test_the_seen_store_is_opened_with_its_minor_version():
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    factory = source.split("def seen_store(")[1].split("\n\n\n")[0]
    assert "SEEN_STORAGE_VERSION" in factory
    assert "minor_version=SEEN_MINOR_VERSION" in factory
    setup = source.split("async def async_setup_entry")[1].split("\nasync def ")[0]
    assert "seen_store(hass, entry.entry_id)" in setup, "uppsättningen öppnar storen genom fabriken"
    # The migration goes by the shape of the file, never by the version.
    migrate = source.split("async def _async_migrate_func")[1].split("\n\n")[0]
    assert "migrate_seen(old_data)" in migrate
