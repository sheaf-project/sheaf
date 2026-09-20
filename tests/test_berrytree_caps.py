"""Pure unit tests for the BerryTree importer's preview pass.

No stack, no crypto, no database: these call `preview` on a plain dict and
assert on the summary. They cover the business-cap prediction
(`limit_warnings`), the honest-reporting behaviour that is the whole point
of this importer (counting the sections it cannot read instead of quietly
dropping them), and the parsing tolerance the format demands.

The run-path clamp is covered end to end by test_imports_berrytree_runner.py.
"""

from __future__ import annotations

from sheaf.services.berrytree_import import KNOWN_SCHEMA_VERSION, preview


def _export(**sections) -> dict:
    """A minimal well-formed BerryTree export, with sections overridden.

    Deliberately carries no email: the fixture mirrors what the importer is
    allowed to look at, and `system.email` is not part of that.
    """
    base = {
        "app": "BerryTree",
        "schema_version": KNOWN_SCHEMA_VERSION,
        "exported_at": "2026-09-16T16:18:28.837880+00:00",
        "system": {"username": "tester", "system_name": "Test System"},
        "members": [],
        "front_entries": [],
        "custom_statuses": [],
        "folders": [],
        "system_contexts": [],
        "layers": [],
        "_partial_errors": [],
    }
    base.update(sections)
    return base


def _member(**overrides) -> dict:
    base = {
        "id": "m1",
        "name": "Alpha",
        "display_name": "",
        "pronouns": "",
        "role": "",
        "description": "",
        "color": "#818CF8",
        "avatar": "",
        "emoji": "",
        "is_private": False,
        "custom_fields": [],
        "tags": [],
        "folder_ids": [],
        "is_template": False,
        "archived": False,
        "counts_toward_headcount": True,
        "created_at": "2026-09-16T16:16:57.762230+00:00",
    }
    base.update(overrides)
    return base


# --- Business caps ---------------------------------------------------------


def test_preview_flags_over_cap_member_name():
    """A member name past the 100-char cap shows up in limit_warnings, so the
    user is warned before the import shortens it."""
    summary = preview(_export(members=[_member(name="n" * 250)]))
    assert any("member name" in w for w in summary.limit_warnings)


def test_preview_flags_over_cap_folder_and_tag_names():
    summary = preview(
        _export(
            members=[_member(tags=["t" * 200])],
            folders=[{"id": "f1", "name": "f" * 200}],
        )
    )
    joined = " ".join(summary.limit_warnings)
    assert "group name" in joined
    assert "tag name" in joined


def test_preview_flags_over_cap_custom_field_name():
    summary = preview(
        _export(
            members=[
                _member(custom_fields=[{"name": "c" * 200, "value": "something"}])
            ]
        )
    )
    assert any("custom field name" in w for w in summary.limit_warnings)


def test_clean_export_has_no_limit_warnings():
    summary = preview(_export(members=[_member()]))
    assert summary.limit_warnings == []


# --- Honest reporting of what we cannot read -------------------------------


def test_preview_counts_unsupported_sections_instead_of_ignoring_them():
    """The sections this importer does not map are reported with counts.

    This is the core contract: a BerryTree export carrying journals must
    never look like a clean import that happened to bring nothing across.
    """
    summary = preview(
        _export(
            members=[_member()],
            journal=[{"id": "j1"}, {"id": "j2"}],
            chat=[{"id": "c1"}],
        )
    )
    by_name = {s.name: s.count for s in summary.unsupported_sections}
    assert by_name["journal entries"] == 2
    assert by_name["chat messages"] == 1
    warning = " ".join(summary.limit_warnings)
    assert "2 journal entries" in warning
    assert "1 chat messages" in warning
    assert "get in touch" in warning


def test_fully_covered_export_reports_no_gaps():
    """An export using only the sections we map says nothing about gaps."""
    summary = preview(_export(members=[_member()], folders=[{"id": "f1", "name": "F"}]))
    assert summary.unsupported_sections == []
    assert not any("Left behind" in w for w in summary.limit_warnings)


def test_extra_contexts_and_layers_are_reported_past_the_first():
    """Every export has one main context and a default layer, so those carry
    no information. A second of either means real structure we cannot map."""
    one_each = preview(
        _export(
            system_contexts=[{"id": "c1", "kind": "main", "name": "Main"}],
            layers=[{"id": "l1", "name": "Default"}],
        )
    )
    assert one_each.unsupported_sections == []

    two_each = preview(
        _export(
            system_contexts=[
                {"id": "c1", "kind": "main", "name": "Main"},
                {"id": "c2", "kind": "alt", "name": "Alt"},
            ],
            layers=[{"id": "l1", "name": "Default"}, {"id": "l2", "name": "Deep"}],
        )
    )
    by_name = {s.name: s.count for s in two_each.unsupported_sections}
    assert by_name["extra system contexts"] == 1
    assert by_name["layers"] == 1


def test_export_errors_are_surfaced_and_bounded():
    """BerryTree records its own export failures; the preview shows them,
    capped in count and length because the text is attacker-sized."""
    summary = preview(
        _export(_partial_errors=["could not write chat", "x" * 5000] + ["e"] * 50)
    )
    assert len(summary.export_errors) == 20
    assert summary.export_errors[0] == "could not write chat"
    assert all(len(e) <= 200 for e in summary.export_errors)


def test_schema_version_mismatch_warns_without_failing():
    summary = preview(_export(schema_version=99))
    assert summary.schema_version == 99
    assert any("schema version 99" in w for w in summary.limit_warnings)


def test_known_schema_version_does_not_warn():
    summary = preview(_export())
    assert not any("schema version" in w for w in summary.limit_warnings)


# --- Classification and tolerance ------------------------------------------


def test_custom_statuses_split_into_fronting_types_and_custom_fronts():
    """BerryTree keeps two different things in `custom_statuses`. Only the
    "status" rows are roster members; "type" rows annotate a front."""
    summary = preview(
        _export(
            custom_statuses=[
                {"id": "t1", "name": "Co-conscious", "kind": "type"},
                {"id": "s1", "name": "Asleep", "kind": "status"},
            ]
        )
    )
    assert summary.fronting_type_count == 1
    assert summary.custom_front_count == 1
    assert [cf.name for cf in summary.custom_fronts] == ["Asleep"]


def test_unknown_custom_status_kind_becomes_a_custom_front():
    """An unrecognised `kind` surfaces as a roster row the user can delete,
    rather than vanishing."""
    summary = preview(
        _export(custom_statuses=[{"id": "x1", "name": "Mystery", "kind": "wat"}])
    )
    assert summary.custom_front_count == 1


def test_templates_are_counted_separately_from_members():
    summary = preview(
        _export(members=[_member(), _member(id="m2", is_template=True)])
    )
    assert summary.member_count == 1
    assert summary.template_count == 1


def test_system_name_prefers_the_main_context():
    """BerryTree keeps the real profile on the main system_context, not on
    the thin top-level `system` object."""
    summary = preview(
        _export(
            system_contexts=[
                {"id": "c1", "kind": "main", "name": "The Real Name"},
            ]
        )
    )
    assert summary.system_name == "The Real Name"


def test_malformed_sections_do_not_raise():
    """Every section walk is defensive: a section that is a string, a list of
    scalars, or a list of nulls must not take the preview down."""
    summary = preview(
        _export(
            members="not a list",
            custom_statuses=[None, 3, "nope"],
            folders=[{"id": "f1", "name": None}],
            front_entries=None,
            _partial_errors="also not a list",
        )
    )
    assert summary.member_count == 0
    assert summary.export_errors == []


def test_tag_and_field_shapes_are_tolerated_and_unknowns_are_not_invented():
    """Tags may be plain names or objects; custom fields may name themselves
    or reference a template. A field entry that yields neither name nor value
    is not guessed at (it is counted at import time instead)."""
    summary = preview(
        _export(
            field_templates=[{"id": "ft1", "name": "Birthday"}],
            members=[
                _member(
                    tags=["plain", {"name": "object"}],
                    custom_fields=[
                        {"name": "Likes", "value": "lasers"},
                        {"field_id": "ft1", "value": "1970-01-01"},
                        {"whatever": "unreadable"},
                    ],
                )
            ],
        )
    )
    assert summary.tag_count == 2
    # Likes + Birthday. The unreadable third entry contributes nothing.
    assert summary.custom_field_count == 2


def test_role_and_mood_count_as_custom_fields():
    """Sheaf has no column for either, so they land as text fields rather
    than being dropped."""
    summary = preview(_export(members=[_member(role="host", mood="tired")]))
    assert summary.custom_field_count == 2
