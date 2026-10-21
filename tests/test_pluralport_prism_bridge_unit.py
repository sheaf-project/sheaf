"""A Prism PluralPort export's typed fields come back out of `json`. Pure.

PluralPort v0.1 has no range type, so Prism writes its slider, scale,
number, boolean and member-reference fields as `field_type: "json"` and
parks the real definition under `extensions.prism.native`. The bridge in
pluralport_import recovers them through the native Prism translator. This is
a stopgap until the spec carries a range field (see the FIXME there).
"""

import json

from sheaf.services.pluralport_import import to_native


def _envelope(fields: list[dict], values: list[dict]) -> dict:
    return {
        "pluralport_version": "0.1",
        "exported_at": "2026-10-20T00:00:00Z",
        "producer": {"app": "Prism", "app_id": "prism", "exporter_version": "0.1.0"},
        "systems": [{"id": "s1", "name": "Sys"}],
        "members": [{"id": "m1", "system_id": "s1", "name": "Ember"}],
        "custom_fields": fields,
        "custom_field_values": values,
    }


def _prism_field(fid: str, name: str, type_id: str, config: dict) -> dict:
    return {
        "id": fid,
        "system_id": "s1",
        "name": name,
        "field_type": "json",
        "extensions": {
            "prism": {
                "native": {
                    "id": fid,
                    "name": name,
                    "fieldTypeId": type_id,
                    "typeConfigJson": json.dumps(config),
                }
            }
        },
    }


def _value(vid: str, fid: str, value) -> dict:
    return {
        "id": vid, "field_id": fid, "subject_type": "member", "subject_id": "m1", "value": value,
    }


def test_prism_slider_behind_json_becomes_a_slider_with_its_config():
    env = _envelope(
        [
            _prism_field(
                "f1",
                "Mood",
                "slider",
                {"mode": "labeled", "leftLabel": "Low", "rightLabel": "High",
                 "gradientPresetId": "sad-happy"},
            )
        ],
        [_value("v1", "f1", 49.6)],
    )
    (field,) = to_native(env)["custom_fields"]
    assert field["field_type"] == "slider"
    assert field["options"]["labels"] == {"left": "Low", "center": None, "right": "High"}
    assert field["options"]["gradient"] == ["#5479AE", "#E4C44F"]
    assert field["values"] == [{"member_id": "m1", "value": 49.6}]


def test_prism_scale_behind_json_becomes_a_numeric_slider():
    env = _envelope(
        [
            _prism_field(
                "f2", "Stars", "scale", {"steps": 5, "stepLabels": ["Meh", "", "", "", "Wow"]}
            )
        ],
        [_value("v2", "f2", 4)],
    )
    (field,) = to_native(env)["custom_fields"]
    assert field["field_type"] == "slider"
    assert (field["options"]["min"], field["options"]["max"]) == (1, 5)
    assert field["values"] == [{"member_id": "m1", "value": 4}]


def test_prism_folder_is_not_a_field_and_member_reference_is_text():
    env = _envelope(
        [
            _prism_field("g1", "Identity", "group", {}),
            _prism_field("f3", "Partner", "member", {}),
        ],
        [_value("v3", "f3", "m2")],
    )
    fields = {f["name"]: f for f in to_native(env)["custom_fields"]}
    assert "Identity" not in fields
    assert fields["Partner"]["field_type"] == "text"


def test_a_json_field_from_anyone_else_is_left_alone():
    env = _envelope(
        [{"id": "f4", "system_id": "s1", "name": "Blob", "field_type": "json",
          "options": {"anything": 1}}],
        [_value("v4", "f4", {"k": "v"})],
    )
    (field,) = to_native(env)["custom_fields"]
    assert field["field_type"] == "json"
    assert field["values"] == [{"member_id": "m1", "value": {"k": "v"}}]
