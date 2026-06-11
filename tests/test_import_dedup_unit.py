"""Unit tests for the shared member-dedup logic.

Pure logic, no DB and no docker stack: every test constructs detached
Member objects and drives `import_dedup` directly. Covers the bits that
are easy to get subtly wrong - pk-id-before-name-hash matching, the
is_custom_front scoping of the name-hash index, the update field policy,
intra-batch dedup, and the cap-sizing count.
"""

from __future__ import annotations

import uuid

from sheaf.models.member import Member
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    MemberMatchIndex,
    candidate_key,
    count_new_members,
    resolve_member,
)


def _m(name_hash: str, *, pk_id: str | None = None, is_cf: bool = False, **extra):
    """A detached Member carrying just the attrs dedup reads."""
    return Member(
        id=uuid.uuid4(),
        name_hash=name_hash,
        pluralkit_id=pk_id,
        is_custom_front=is_cf,
        **extra,
    )


# --- MemberMatchIndex.find -------------------------------------------------


def test_find_prefers_pk_id_over_name_hash():
    existing = _m("hashA", pk_id="abcd")
    idx = MemberMatchIndex()
    idx.register(existing)
    # Different name hash, same pk id -> still a match (pk id wins).
    assert (
        idx.find(name_hash="hashZ", pluralkit_id="abcd", is_custom_front=False)
        is existing
    )


def test_find_name_hash_scoped_by_is_custom_front():
    member = _m("shared", is_cf=False)
    idx = MemberMatchIndex()
    idx.register(member)
    # A custom front sharing the name must NOT match the regular member,
    # else update would flip is_custom_front and corrupt the member.
    assert idx.find(name_hash="shared", is_custom_front=True) is None
    assert idx.find(name_hash="shared", is_custom_front=False) is member


def test_find_missing_returns_none():
    idx = MemberMatchIndex()
    assert idx.find(name_hash="nope", is_custom_front=False) is None


def test_register_is_first_wins():
    first = _m("dup", pk_id="zz")
    second = _m("dup", pk_id="zz")
    idx = MemberMatchIndex()
    idx.register(first)
    idx.register(second)
    assert idx.find(name_hash="dup", is_custom_front=False) is first
    assert idx.find(name_hash="x", pluralkit_id="zz", is_custom_front=False) is first


# --- resolve_member --------------------------------------------------------


def test_create_strategy_always_creates_even_on_match():
    idx = MemberMatchIndex()
    idx.register(_m("dup"))
    cand = _m("dup")
    res = resolve_member(cand, index=idx, strategy=ImportConflictStrategy.CREATE)
    assert res.disposition == "created"
    assert res.member is cand


def test_skip_returns_existing_untouched():
    existing = _m("dup", display_name="keep")
    idx = MemberMatchIndex()
    idx.register(existing)
    cand = _m("dup", display_name="ignored")
    res = resolve_member(cand, index=idx, strategy=ImportConflictStrategy.SKIP)
    assert res.disposition == "skipped"
    assert res.member is existing
    assert existing.display_name == "keep"


def test_update_overwrites_set_fields_preserves_unset():
    existing = _m("dup", display_name="old", pronouns="they/them", emoji=None)
    idx = MemberMatchIndex()
    idx.register(existing)
    cand = _m("dup", display_name="new", pronouns=None, emoji="star")
    res = resolve_member(cand, index=idx, strategy=ImportConflictStrategy.UPDATE)
    assert res.disposition == "updated"
    assert res.member is existing
    assert existing.display_name == "new"      # candidate had a value -> overwrite
    assert existing.pronouns == "they/them"    # candidate None -> preserved
    assert existing.emoji == "star"            # candidate set -> filled in


def test_no_match_creates_and_registers_for_intra_batch():
    idx = MemberMatchIndex()
    first = _m("dup")
    r1 = resolve_member(first, index=idx, strategy=ImportConflictStrategy.SKIP)
    assert r1.disposition == "created"
    # A later row in the same batch with the same key dedups against the
    # one just created, not a fresh insert.
    second = _m("dup")
    r2 = resolve_member(second, index=idx, strategy=ImportConflictStrategy.SKIP)
    assert r2.disposition == "skipped"
    assert r2.member is first


def test_member_and_custom_front_same_name_both_created():
    idx = MemberMatchIndex()
    member = _m("alex", is_cf=False)
    cf = _m("alex", is_cf=True)
    r1 = resolve_member(member, index=idx, strategy=ImportConflictStrategy.SKIP)
    r2 = resolve_member(cf, index=idx, strategy=ImportConflictStrategy.SKIP)
    assert r1.disposition == "created"
    assert r2.disposition == "created"  # different scope, no false match


# --- count_new_members (cap sizing) ----------------------------------------


def test_count_new_members_excludes_existing_and_intra_batch_dupes():
    idx = MemberMatchIndex()
    idx.register(_m("exists"))
    keys = [
        ("exists", None, False),  # already in system -> not new
        ("fresh", None, False),   # new
        ("fresh", None, False),   # intra-batch dup of the previous -> not new
        ("fresh", None, True),    # custom front, different scope -> new
    ]
    assert count_new_members(keys, index=idx, strategy=ImportConflictStrategy.SKIP) == 2


def test_count_new_members_pk_id_path():
    idx = MemberMatchIndex()
    idx.register(_m("hashA", pk_id="abcd"))
    keys = [
        ("hashZ", "abcd", False),  # matches existing by pk id -> not new
        ("hashY", "wxyz", False),  # new pk id
        ("hashY", "wxyz", False),  # intra-batch dup pk id -> not new
    ]
    assert count_new_members(keys, index=idx, strategy=ImportConflictStrategy.UPDATE) == 1


def test_count_new_members_create_counts_everything():
    idx = MemberMatchIndex()
    idx.register(_m("exists"))
    keys = [("exists", None, False), ("exists", None, False)]
    assert count_new_members(keys, index=idx, strategy=ImportConflictStrategy.CREATE) == 2


def test_candidate_key_shape():
    m = _m("h", pk_id="abcd", is_cf=True)
    assert candidate_key(m) == ("h", "abcd", True)
