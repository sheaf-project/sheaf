"""The shape an import writes into `custom_field_definitions.options`.

Pure, no stack. The API stores options as `dict | None` and its read schema
refuses anything else, so every importer that builds definitions directly
(native, archive, PluralPort) has to land on that shape or the account's
field list stops serving. The clamp is the one place they all go through.
"""

from __future__ import annotations

from sheaf.models.custom_field import FieldType
from sheaf.services.import_limits import ClampReport
from sheaf.services.sheaf_import import _clamp_field_options


def _clamp(options: object, field_type: FieldType) -> object:
    return _clamp_field_options(options, ClampReport(), field_type)


def test_sheaf_shape_passes_through_for_the_choice_types():
    assert _clamp({"choices": ["a", "b"]}, FieldType.SELECT) == {"choices": ["a", "b"]}
    assert _clamp({"choices": ["a"]}, FieldType.MULTISELECT) == {"choices": ["a"]}


def test_string_array_shape_becomes_choices():
    """What Prism writes, and what the spec allows alongside the object."""
    assert _clamp(["a", "b"], FieldType.SELECT) == {"choices": ["a", "b"]}
    assert _clamp(["a"], FieldType.MULTISELECT) == {"choices": ["a"]}


def test_choice_options_without_a_usable_list_are_dropped():
    for junk in (None, "a,b", 3, {"other": 1}, {"choices": "a"}):
        assert _clamp(junk, FieldType.SELECT) is None, junk


def test_extra_keys_on_a_choice_object_do_not_survive():
    # The API only accepts `choices`; an import should not be able to store
    # keys a POST would have refused.
    assert _clamp({"choices": ["a"], "colour": "#fff"}, FieldType.SELECT) == {"choices": ["a"]}


def test_types_without_options_always_store_none():
    for ft in (FieldType.TEXT, FieldType.NUMBER, FieldType.DATE, FieldType.BOOLEAN):
        assert _clamp(["a"], ft) is None
        assert _clamp({"choices": ["a"]}, ft) is None
        assert _clamp(None, ft) is None


def test_oversized_choice_lists_are_capped_in_either_shape():
    from sheaf.services import import_limits as il

    too_many = [f"c{i}" for i in range(il.CF_CHOICES_COUNT.limit + 5)]
    assert len(_clamp(too_many, FieldType.SELECT)["choices"]) == il.CF_CHOICES_COUNT.limit
    assert (
        len(_clamp({"choices": too_many}, FieldType.SELECT)["choices"])
        == il.CF_CHOICES_COUNT.limit
    )
