"""A model the integration does not know says which family it belongs to (R19, the report).

The report sends the indoor unit as a slug from a closed list, and anything
not on it as "other", so a new kind of controller turning up in the field was
invisible until its owner filed an issue. For a model reported as other the
report now carries family_<stem>, the stem of the display's settings file,
and only when the stem has the shape of one. Where the stem comes from: the
entry's settings_stem where it was made with one, the settings file's name
that every entry keeps, and for an entry made before either, the parentheses
at the end of its model name, where the stem of an unknown family was put.
"""

from __future__ import annotations


def _features(stats_extra, model, stem):
    return stats_extra.build_extra(
        model, has_display=True, control_enabled=True, page_count=1, read_failures=0,
        settings_stem=stem,
    )["features"]


def _families(features):
    return {key for key in features if key.startswith("family_")}


# ----------------------------------------------------- which models send it


def test_an_unknown_model_sends_its_family(stats_extra):
    assert _families(_features(stats_extra, "EcoZenith (ezi4xx)", "ezi4xx")) == {"family_ezi4xx"}
    # The name an entry made before R19 carries for the same family.
    assert _families(_features(stats_extra, "CTC (ezi4xx)", "ezi4xx")) == {"family_ezi4xx"}


def test_a_known_model_sends_no_family(stats_extra):
    for model, stem in (
        ("EcoZenith i255", "ezi2xx"),
        ("EcoZenith i360", "ezi3xx"),
        ("EcoZenith i550 Pro", "ezi5xx"),
        ("EcoLogic", "ecologic"),
    ):
        assert not _families(_features(stats_extra, model, stem)), model


def test_no_model_at_all_sends_no_family(stats_extra):
    # "unknown" is a set-up that never got as far as the display, not a family.
    assert not _families(_features(stats_extra, None, "ezi4xx"))


def test_an_unknown_model_without_a_stem_sends_nothing(stats_extra):
    assert not _families(_features(stats_extra, "EcoZenith (ezi4xx)", None))


# --------------------------------------------- only the shape of a stem


def test_anything_but_the_shape_of_a_stem_is_left_out(stats_extra):
    for stem in ("x", "a" * 17, "ezi-4xx", "ezi 4xx", "ezi4xx.bin", "ezi_4xx", "", "  "):
        assert not _families(_features(stats_extra, "CTC (whatever)", stem)), repr(stem)
    assert not _families(_features(stats_extra, "CTC (whatever)", 42))


def test_a_stem_in_capitals_is_sent_in_lower_case(stats_extra):
    assert _families(_features(stats_extra, "CTC (EZI4XX)", " EZI4XX ")) == {"family_ezi4xx"}


def test_the_longest_family_key_fits_the_backends_pattern(stats_extra):
    key = stats_extra.family_feature("other", "a" * 16)
    assert key == "family_" + "a" * 16
    assert len(key) <= 32


# -------------------------------------------- where the stem comes from


def test_the_stem_given_at_set_up_comes_first(stats_extra):
    data = {
        "settings_stem": "ezi4xx",
        "settings_name": "settings_ezi9xx.bin",
        "model": "EcoZenith (ezi8xx)",
    }
    assert stats_extra.entry_stem(data) == "ezi4xx"


def test_then_the_settings_file_every_entry_keeps(stats_extra):
    data = {"settings_name": "settings_ezi4xx.bin", "model": "CTC (ezi8xx)"}
    assert stats_extra.entry_stem(data) == "ezi4xx"


def test_then_the_model_names_parentheses(stats_extra):
    assert stats_extra.entry_stem({"model": "CTC (ezi4xx)"}) == "ezi4xx"
    assert stats_extra.entry_stem({"model": "EcoZenith (ezi4xx)", "settings_name": ""}) == "ezi4xx"


def test_an_entry_with_nothing_to_go_by_has_no_stem(stats_extra):
    assert stats_extra.entry_stem({"model": "EcoZenith i255"}) is None
    assert stats_extra.entry_stem({}) is None
    assert stats_extra.entry_stem(None) is None


def test_the_whole_path_from_an_entry_to_the_flag(stats_extra):
    # An entry made before settings_stem existed, for a family nobody knew.
    data = {"model": "CTC (ezi4xx)", "settings_name": "settings_ezi4xx.bin"}
    features = _features(stats_extra, data["model"], stats_extra.entry_stem(data))
    assert features["family_ezi4xx"] is True
