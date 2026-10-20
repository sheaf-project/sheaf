"""The slider custom field type's options contract and value rules. Pure."""

import pytest

from sheaf.services import slider_fields as sf


def test_no_options_means_a_labeled_0_to_100_slider():
    assert sf.normalise_options(None) == {
        "mode": "labeled",
        "min": 0,
        "max": 100,
        "step": 1,
        "unit": None,
        "labels": {"left": None, "center": None, "right": None},
        "gradient": None,
    }


def test_labeled_mode_pins_the_range_and_fills_every_key():
    out = sf.normalise_options(
        {"mode": "labeled", "labels": {"left": " Soft ", "right": "Hard"}}
    )
    assert out["min"] == 0 and out["max"] == 100
    assert out["labels"] == {"left": "Soft", "center": None, "right": "Hard"}
    assert out["unit"] is None and out["gradient"] is None


def test_numeric_mode_keeps_bounds_step_and_unit():
    out = sf.normalise_options(
        {"mode": "numeric", "min": 120, "max": 220.0, "step": 0.5, "unit": " cm "}
    )
    assert out["min"] == 120 and out["max"] == 220 and out["step"] == 0.5
    assert out["unit"] == "cm"


def test_gradient_colours_are_validated_and_upper_cased():
    out = sf.normalise_options({"gradient": ["#e89bb8", "#8faa9a"]})
    assert out["gradient"] == ["#E89BB8", "#8FAA9A"]
    assert sf.normalise_options({"gradient": []})["gradient"] is None


@pytest.mark.parametrize(
    "bad",
    [
        {"mode": "dial"},
        {"nope": 1},
        {"mode": "numeric", "min": 5, "max": 5},
        {"mode": "numeric", "min": 0, "max": 10, "step": 0},
        {"mode": "numeric", "min": 0, "max": 10, "step": 11},
        {"gradient": ["#FFF"]},
        {"gradient": ["red", "#000000"]},
        {"gradient": ["#000000"] * 9},
        {"labels": {"top": "x"}},
        {"labels": {"left": "x" * 51}},
        {"mode": "numeric", "unit": "x" * 13},
        {"mode": "numeric", "min": "0", "max": 10},
        {"mode": "numeric", "min": True, "max": 10},
    ],
)
def test_bad_options_are_refused_not_stored(bad):
    with pytest.raises(ValueError):
        sf.normalise_options(bad)


def test_values_must_be_numbers_inside_the_range():
    opts = sf.normalise_options({"mode": "numeric", "min": 1, "max": 5})
    sf.validate_value(1, opts)
    sf.validate_value(4.5, opts)
    for bad in (0, 6, "3", True, None, float("nan")):
        with pytest.raises((ValueError, TypeError)):
            sf.validate_value(bad, opts)


def test_imported_values_are_clamped_not_dropped():
    opts = sf.normalise_options(None)
    assert sf.clamp_value("49.6098104", opts) == 49.6098104
    assert sf.clamp_value(101, opts) == 100
    assert sf.clamp_value(-3, opts) == 0
    assert sf.clamp_value("62", opts) == 62
    assert sf.clamp_value("lots", opts) is None
    assert sf.clamp_value(True, opts) is None


def test_native_reimport_normalises_slider_options_like_the_api():
    """A Sheaf export is user-supplied too: its slider options go through the
    same normaliser as a POST /v1/fields, and an unusable set falls back to
    the defaults rather than landing verbatim."""
    from sheaf.models.custom_field import FieldType
    from sheaf.services.import_limits import ClampReport
    from sheaf.services.sheaf_import import _clamp_field_options

    report = ClampReport()
    ok = _clamp_field_options(
        {"mode": "numeric", "min": 1, "max": 5, "gradient": ["#abcdef", "#000000"]},
        report,
        FieldType.SLIDER,
    )
    assert ok["gradient"] == ["#ABCDEF", "#000000"] and ok["step"] is None
    bad = _clamp_field_options(
        {"mode": "numeric", "min": 9, "max": 1, "labels": {"left": "x" * 500}},
        report,
        FieldType.SLIDER,
    )
    assert bad == sf.default_options()
    # Other types are untouched by this branch.
    assert _clamp_field_options({"choices": ["a"]}, report, FieldType.SELECT) == {
        "choices": ["a"]
    }


def test_readings_say_percent_or_unit():
    assert sf.format_value(62, None) == "62%"
    assert sf.format_value(49.6098104, None) == "49.61%"
    numeric = sf.normalise_options({"mode": "numeric", "min": 0, "max": 300, "unit": "cm"})
    assert sf.format_value(172.5, numeric) == "172.5 cm"
    bare = sf.normalise_options({"mode": "numeric", "min": 1, "max": 5})
    assert sf.format_value(3.0, bare) == "3"
    assert sf.format_value(None, None) == ""
