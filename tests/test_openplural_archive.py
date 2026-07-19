"""Unit tests for OpenPlural import-residual preservation (the baseline
passthrough tier). Pure functions: extraction, compress+encrypt pack/unpack,
size cap, merge, per-record detection, and re-merge on export.
"""

from __future__ import annotations

import uuid

from sheaf.services.openplural_archive import (
    extract_residual,
    has_per_record_foreign_extensions,
    merge_residual,
    pack_residual,
    unpack_residual,
)
from sheaf.services.openplural_export import build_envelope
from sheaf.services.openplural_import import to_native

_EXPORTED_AT = "2026-06-20T00:00:00+00:00"


def _foreign_envelope() -> dict:
    """An envelope as if produced by another app: carries data Sheaf does
    not model (foreign extensions, chat + relationships modules,
    front_comments, non-tag taxonomy)."""
    return {
        "openplural_version": "0.1",
        "producer": {"app": "Prism", "app_id": "prism"},
        "systems": [{"id": "s1", "name": "Foreign", "privacy": "public"}],
        "members": [{"id": "m1", "name": "Iris", "privacy": "private"}],
        "taxonomy_terms": [
            {"id": "t1", "kind": "tag", "name": "host"},
            {"id": "r1", "kind": "role", "name": "protector"},
        ],
        "taxonomy_assignments": [
            {"term_id": "t1", "subject_type": "member", "subject_id": "m1"},
            {"term_id": "r1", "subject_type": "member", "subject_id": "m1"},
        ],
        "chat": {"conversations": [{"id": "c1"}], "messages": [{"id": "x1", "body": "hi"}]},
        "relationships": {"edges": [{"a": "m1", "b": "m1", "type": "self"}]},
        "front_comments": [{"id": "fc1", "body": "comment"}],
        "extensions": {
            "sheaf": {"lineage": []},
            "prism": {"theme": "dark", "legacyCoFronters": [1, 2]},
        },
    }


def test_extract_residual_captures_unsupported():
    res = extract_residual(_foreign_envelope())
    assert res["extensions"] == {"prism": {"theme": "dark", "legacyCoFronters": [1, 2]}}
    assert "sheaf" not in res["extensions"]
    assert res["chat"]["messages"][0]["body"] == "hi"
    assert res["relationships"]["edges"][0]["type"] == "self"
    assert res["front_comments"][0]["body"] == "comment"
    # Only the non-tag term + its assignment are preserved.
    assert [t["id"] for t in res["taxonomy_terms"]] == ["r1"]
    assert [a["term_id"] for a in res["taxonomy_assignments"]] == ["r1"]


def test_extract_residual_empty_for_native_file():
    # A Sheaf-produced envelope has only our own extensions namespace.
    native_env = build_envelope(
        {"version": "2", "system": {"name": "S"}, "members": []},
        exported_at=_EXPORTED_AT,
    )
    assert extract_residual(native_env) == {}


def test_pack_unpack_round_trip():
    res = extract_residual(_foreign_envelope())
    system_id = uuid.uuid4()
    token, warning = pack_residual(res, max_bytes=8 * 1024 * 1024, system_id=system_id)
    assert warning is None
    assert isinstance(token, str) and token
    assert unpack_residual(token, system_id=system_id) == res


def test_pack_respects_size_cap():
    big = {"extensions": {"prism": {"blob": "x" * 5000}}}
    token, warning = pack_residual(big, max_bytes=1024, system_id=uuid.uuid4())
    assert token is None
    assert warning is not None and "exceeds" in warning


def test_pack_empty_is_noop():
    system_id = uuid.uuid4()
    assert pack_residual({}, max_bytes=1024, system_id=system_id) == (None, None)
    assert unpack_residual(None, system_id=system_id) == {}
    # corrupt -> empty, no raise
    assert unpack_residual("not-a-real-token", system_id=system_id) == {}


def test_merge_residual_unions_namespaces():
    a = {"extensions": {"prism": {"x": 1}}, "chat": {"messages": []}}
    b = {"extensions": {"pluralkit": {"y": 2}}, "relationships": {"edges": []}}
    merged = merge_residual(a, b)
    assert merged["extensions"] == {"prism": {"x": 1}, "pluralkit": {"y": 2}}
    assert "chat" in merged and "relationships" in merged


def test_has_per_record_foreign_extensions():
    env = {"members": [{"id": "m1", "extensions": {"prism": {"color": "#f00"}}}]}
    assert has_per_record_foreign_extensions(env) is True
    env_own = {"members": [{"id": "m1", "extensions": {"sheaf": {"emoji": "X"}}}]}
    assert has_per_record_foreign_extensions(env_own) is False


def test_preserved_residual_re_merges_on_export():
    """The full round-trip: residual extracted from a foreign file, carried
    on the (native) system as a plain dict, comes back out in the envelope."""
    residual = extract_residual(_foreign_envelope())
    native = to_native(
        {"openplural_version": "0.1", "systems": [{"id": "s1", "name": "S"}], "members": []}
    )
    native["system"]["openplural_archive"] = residual
    env = build_envelope(native, exported_at=_EXPORTED_AT)
    # Foreign extensions ride alongside Sheaf's namespace.
    assert env["extensions"]["prism"] == {"theme": "dark", "legacyCoFronters": [1, 2]}
    assert "sheaf" in env["extensions"]
    # Whole modules restored + advertised in capabilities.
    assert env["chat"]["messages"][0]["body"] == "hi"
    assert env["relationships"]["edges"][0]["type"] == "self"
    assert "chat" in env["capabilities"]["modules"]
    assert "relationships" in env["capabilities"]["modules"]
    # front_comments + non-tag taxonomy folded back in.
    assert env["front_comments"][0]["body"] == "comment"
    assert any(t["id"] == "r1" for t in env["taxonomy_terms"])
    assert any(a["term_id"] == "r1" for a in env["taxonomy_assignments"])
