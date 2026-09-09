"""Pure unit tests for Masquerade import business-cap warnings.

These exercise the preview measure pass only (no DB / job runner),
confirming that over-cap Masquerade fields surface in `limit_warnings` so
the user is warned the import will shorten them before they confirm. The
clamp the import actually applies uses the same caps, so these double as a
regression guard on both.
"""

from sheaf.services.masquerade_import import preview


def test_preview_flags_over_cap_name_and_display_name():
    """A profile name and display_name past the 100-char cap both surface."""
    data = {
        "profiles": [
            {"name": "A" * 200, "display_name": "d" * 150},
            {"name": "Fine"},
        ],
    }
    summary = preview(data)
    joined = " ".join(summary.limit_warnings)
    assert "member name" in joined
    assert "member display name" in joined


def test_preview_clean_export_has_no_limit_warnings():
    data = {
        "profiles": [
            {
                "name": "Alpha",
                "display_name": "The Alpha",
                "avatar_url": "https://cdn.example.com/a.png",
                "color": "#f472b6",
                "hidden": True,
                "tags": [{"prefix": "a:", "suffix": None}],
            },
        ],
    }
    summary = preview(data)
    assert summary.limit_warnings == []
    assert summary.member_count == 1
    assert summary.members[0].name == "Alpha"
    assert summary.members[0].id == "0"


def test_preview_survives_malformed_entries():
    """Wrong-typed profiles and fields don't raise: non-dict rows are
    dropped, non-string names read as 'unnamed', and a non-list `profiles`
    counts as empty."""
    summary = preview(
        {
            "profiles": [
                "not a dict",
                42,
                {"name": 12345, "display_name": {"x": 1}, "tags": "nope"},
                {"name": None},
            ],
        }
    )
    assert summary.member_count == 2
    assert {m.name for m in summary.members} == {"unnamed"}
    assert summary.limit_warnings == []

    assert preview({"profiles": {"a": 1}}).member_count == 0
    assert preview({}).member_count == 0
