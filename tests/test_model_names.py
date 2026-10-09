"""What a settings file is called, and that the two lists of models agree (R19, the name).

The display names its family by a settings file, settings_ezi2xx.bin on an
i255. An unknown file used to give the model "CTC (<stem>)", so the device was
"CTC CTC (<stem>)"; it is now "EcoZenith (<stem>)", the right sort of name for
any CTC controller with this display, still showing which stem turned up. New
entries keep the stem in their data for the report to read.

discovery.MODEL_NAMES gives the names and stats_extra.MODEL_SLUGS turns them
into the report's closed list of slugs. A name in the one and not the other
would report a known model as "other" without anybody noticing.
"""

from __future__ import annotations

import pytest

from conftest import COMPONENT


@pytest.mark.parametrize(
    ("settings", "model"),
    [
        ("settings_ezi2xx.bin", "EcoZenith i255"),
        ("settings_ezi3xx.bin", "EcoZenith i360"),
        ("settings_ezi5xx.bin", "EcoZenith i550 Pro"),
        ("settings_ecologic.bin", "EcoLogic"),
        ("settings_ezi4xx.bin", "EcoZenith (ezi4xx)"),
        ("settings_gsi12.bin", "EcoZenith (gsi12)"),
    ],
)
def test_a_settings_file_names_its_model(discovery, settings, model):
    found = discovery.DiscoveredDisplay(host="192.168.1.55", settings_name=settings)
    assert found.model == model
    assert not found.model.startswith("CTC"), "enhetsnamnet blir annars CTC CTC"


def test_the_stem_is_the_family_part_of_the_file(discovery):
    assert discovery.settings_stem("settings_ezi2xx.bin") == "ezi2xx"
    found = discovery.DiscoveredDisplay(host="192.168.1.55", settings_name="settings_ezi4xx.bin")
    assert found.stem == "ezi4xx"


def test_a_stem_too_strange_for_a_name_gives_the_family_alone(discovery):
    # The model becomes the device's name and the prefix of every entity id.
    assert discovery.model_name("ezi 2xx/../x") == "EcoZenith"
    assert discovery.model_name("x" * 40) == "EcoZenith"
    assert discovery.model_name("") == "EcoZenith"


def test_every_named_model_has_a_slug_and_every_slug_a_name(discovery, stats_extra):
    assert set(discovery.MODEL_NAMES.values()) == set(stats_extra.MODEL_SLUGS)
    for name in discovery.MODEL_NAMES.values():
        assert stats_extra.model_slug(name) != "other", name


def test_an_unknown_family_reports_as_other_and_never_by_its_name(stats_extra):
    assert stats_extra.model_slug("EcoZenith (ezi4xx)") == "other"
    assert stats_extra.model_slug("EcoZenith") == "other"


def test_new_entries_keep_the_stem():
    flow = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    data = flow.split("def _entry_data(")[1].split("\n    def ")[0]
    assert "data[CONF_SETTINGS_STEM] = settings_stem(self._settings_name)" in data
    const = (COMPONENT / "const.py").read_text(encoding="utf-8")
    assert 'CONF_SETTINGS_STEM: Final = "settings_stem"' in const
