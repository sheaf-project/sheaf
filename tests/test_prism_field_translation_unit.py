"""How Prism custom fields and values become Sheaf ones. Pure, no stack.

The bug this pins: a Prism "choice" field exports its options inside
`typeConfigJson` and each value as a JSON blob of option ids. Imported as
text, every member showed `{"options":["<uuid>"]}` instead of the label.
"""

import json
import uuid

from sheaf.models.custom_field import FieldType
from sheaf.services.prism_import import (
    _translate_prism_field,
    _translate_prism_value,
)

OPT_A, OPT_B, OPT_C = (str(uuid.uuid4()) for _ in range(3))


def _choice_field(*, multiple: bool, with_deleted: bool = False) -> dict:
    options = [
        {"id": OPT_B, "label": "Protector", "sortOrder": 1},
        {"id": OPT_A, "label": "Caretaker", "sortOrder": 0, "colorHex": "#AABBCC"},
        {"id": OPT_C, "label": "Little", "sortOrder": 2, "isDeleted": with_deleted},
    ]
    return {
        "id": "f1",
        "name": "Roles",
        "fieldTypeId": "choice",
        "typeConfigJson": json.dumps(
            {"runtimeType": "choice", "options": options, "allowsMultiple": multiple}
        ),
    }


def test_choice_becomes_multiselect_with_labels_in_prism_order():
    pf = _translate_prism_field(_choice_field(multiple=True))
    assert pf.field_type is FieldType.MULTISELECT
    assert pf.options == {"choices": ["Caretaker", "Protector", "Little"]}
    value = json.dumps({"options": [OPT_B, OPT_A]})
    assert _translate_prism_value(value, pf) == ["Protector", "Caretaker"]


def test_single_choice_becomes_select_with_one_label():
    pf = _translate_prism_field(_choice_field(multiple=False))
    assert pf.field_type is FieldType.SELECT
    assert _translate_prism_value(json.dumps({"options": [OPT_A]}), pf) == "Caretaker"


def test_deleted_options_are_not_offered_but_other_text_survives():
    pf = _translate_prism_field(_choice_field(multiple=True, with_deleted=True))
    assert pf.options == {"choices": ["Caretaker", "Protector"]}
    value = json.dumps({"options": [OPT_A], "other": "Gatekeeper"})
    assert _translate_prism_value(value, pf) == ["Caretaker", "Gatekeeper"]
    # Nothing picked and no other: nothing to store.
    assert _translate_prism_value(json.dumps({"options": []}), pf) is None


def test_labeled_slider_keeps_labels_and_resolves_a_preset_gradient():
    pf = _translate_prism_field(
        {
            "id": "f2",
            "name": "Mood",
            "fieldTypeId": "slider",
            "typeConfigJson": json.dumps(
                {
                    "runtimeType": "slider",
                    "mode": "labeled",
                    "leftLabel": "Low",
                    "rightLabel": "High",
                    "gradientPresetId": "sad-happy",
                }
            ),
        }
    )
    assert pf.field_type is FieldType.SLIDER
    assert pf.options["mode"] == "labeled"
    assert pf.options["labels"] == {"left": "Low", "center": None, "right": "High"}
    assert pf.options["gradient"] == ["#5479AE", "#E4C44F"]
    assert _translate_prism_value("49.6098104", pf) == 49.6098104
    assert _translate_prism_value("250", pf) == 100  # clamped, not dropped


def test_numeric_slider_keeps_bounds_unit_and_explicit_colours():
    pf = _translate_prism_field(
        {
            "id": "f3",
            "name": "Height",
            "fieldTypeId": "slider",
            "typeConfigJson": json.dumps(
                {
                    "runtimeType": "slider",
                    "mode": "numeric",
                    "min": 120,
                    "max": 220,
                    "step": 1,
                    "unit": "cm",
                    "leftColorHex": "#111111",
                    "rightColorHex": "#EEEEEE",
                }
            ),
        }
    )
    assert pf.options["mode"] == "numeric"
    assert (pf.options["min"], pf.options["max"], pf.options["unit"]) == (120, 220, "cm")
    assert pf.options["gradient"] == ["#111111", "#EEEEEE"]
    assert _translate_prism_value("172", pf) == 172


def test_a_broken_slider_config_still_imports_as_a_plain_slider():
    pf = _translate_prism_field(
        {
            "id": "f4",
            "name": "Odd",
            "fieldTypeId": "slider",
            "typeConfigJson": json.dumps(
                {"mode": "numeric", "min": 10, "max": 1, "gradientColorsHex": ["bad", "#000000"]}
            ),
        }
    )
    assert pf.field_type is FieldType.SLIDER
    assert pf.options["gradient"] is None


def test_scale_becomes_a_whole_step_numeric_slider():
    pf = _translate_prism_field(
        {
            "id": "f5",
            "name": "Stars",
            "fieldTypeId": "scale",
            "typeConfigJson": json.dumps(
                {
                    "runtimeType": "scale",
                    "emoji": "⭐",
                    "steps": 5,
                    "stepLabels": ["Meh", "", "", "", "Great"],
                }
            ),
        }
    )
    assert pf.field_type is FieldType.SLIDER
    assert (pf.options["min"], pf.options["max"], pf.options["step"]) == (1, 5, 1)
    assert pf.options["labels"]["left"] == "Meh" and pf.options["labels"]["right"] == "Great"
    assert _translate_prism_value("4", pf) == 4


def test_groups_are_folders_and_member_references_are_flagged():
    assert _translate_prism_field({"id": "g", "name": "Identity", "fieldTypeId": "group"}) is None
    pf = _translate_prism_field({"id": "m", "name": "Partner", "fieldTypeId": "member"})
    assert pf.field_type is FieldType.TEXT
    assert pf.choice_labels == {"__surfaced__": "member"}
    for plain in ("text", "long_text", "color"):
        pf = _translate_prism_field({"name": "x", "fieldTypeId": plain})
        assert pf.field_type is FieldType.TEXT


def test_nested_json_bombs_inside_strings_are_refused_not_fatal():
    """The outer file is element-capped, but a type config or a choice value
    is JSON inside a string, which that cap sees as one element. Each nested
    document gets its own cap, and a hostile one degrades the field rather
    than the whole job."""
    bomb = json.dumps(
        {"options": [{"id": str(i), "label": "x"} for i in range(20_000)]}
    )
    pf = _translate_prism_field(
        {"id": "b", "name": "Bomb", "fieldTypeId": "choice", "typeConfigJson": bomb}
    )
    assert pf.field_type in (FieldType.SELECT, FieldType.MULTISELECT)
    assert pf.choice_labels == {}

    # Deep nesting would be a RecursionError out of json.loads; here it is
    # just "not a JSON object", which the choice path treats as a typed-in
    # label (the older export shape) rather than something to choke on.
    deep = "[" * 100_000 + "]" * 100_000
    assert isinstance(_translate_prism_value(deep, pf), str | None)
    pf2 = _translate_prism_field(
        {"id": "d", "name": "Deep", "fieldTypeId": "choice", "typeConfigJson": deep}
    )
    assert pf2.choice_labels == {}
