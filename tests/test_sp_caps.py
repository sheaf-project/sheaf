"""Pure unit tests for SimplyPlural import business-cap warnings.

These exercise the preview measure pass only (no DB / job runner), confirming
that over-cap SP fields surface in `limit_warnings` so the user is warned the
import will shorten them before they confirm. The clamp the import actually
applies uses the same caps, so these doubling as a regression guard on both.
"""

from sheaf.services.sp_import import preview


def test_preview_flags_over_cap_member_name_and_display_name():
    """A member name and displayName past the 100-char cap both surface."""
    data = {
        "members": [
            {"_id": "alpha", "name": "A" * 200, "displayName": "d" * 150},
            {"_id": "beta", "name": "Fine"},
        ],
    }
    summary = preview(data)
    joined = " ".join(summary.limit_warnings)
    assert "member name" in joined
    assert "member display name" in joined


def test_preview_flags_over_cap_group_field_and_custom_front_names():
    """Group name, custom-field name, and custom-front name over their caps
    each surface. Custom fronts share the member-name cap (they are Member
    rows), so an over-cap front name reads as a 'member name' warning."""
    data = {
        "groups": [{"_id": "g1", "name": "g" * 150}],
        "customFields": [{"_id": "f1", "name": "f" * 150}],
        "frontStatuses": [{"_id": "cf1", "name": "c" * 200}],
    }
    summary = preview(data)
    joined = " ".join(summary.limit_warnings)
    assert "group name" in joined
    assert "custom field name" in joined
    assert "member name" in joined  # custom front, capped as a member


def test_preview_flags_over_cap_system_name():
    """System name from settings.systemName over the 100-char cap surfaces."""
    data = {"settings": {"systemName": "S" * 200}, "members": []}
    summary = preview(data)
    assert any("system name" in w for w in summary.limit_warnings)


def test_preview_clean_export_has_no_limit_warnings():
    data = {
        "settings": {"systemName": "Sys"},
        "members": [
            {"_id": "alpha", "name": "Alpha", "displayName": "Al", "pronouns": "they"}
        ],
        "groups": [{"_id": "g1", "name": "Group"}],
        "customFields": [{"_id": "f1", "name": "Pronoun set"}],
    }
    summary = preview(data)
    assert summary.limit_warnings == []


def test_preview_surfaces_over_cap_front_history(monkeypatch):
    """Front-history rows over the per-import cap are predicted in the preview,
    so the user is warned before the job fails (bomb protection)."""
    from sheaf.config import settings

    monkeypatch.setattr(settings, "import_max_fronts", 1)
    data = {
        "members": [{"_id": "a", "name": "A"}],
        "frontHistory": [{"member": "a"}, {"member": "a"}],
    }
    summary = preview(data)
    assert any(
        "2 fronts" in w and "one job" in w for w in summary.limit_warnings
    ), summary.limit_warnings
