"""Unit tests for the shared member-dedup logic.

No docker stack: every test constructs detached Member objects and
drives `import_dedup` directly, with a stub session standing in for the
one query the privacy gate makes. Covers the bits that are easy to get
subtly wrong - pk-id-before-name-hash matching, the is_custom_front
scoping of the name-hash index, the update field policy, the privacy
raise gate, intra-batch dedup, and the cap-sizing count.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sheaf.crypto import decrypt, encrypt
from sheaf.encrypted_fields import member_name_aad
from sheaf.models.member import Member
from sheaf.models.system import PrivacyLevel
from sheaf.services.import_dedup import (
    ImportConflictStrategy,
    MemberMatchIndex,
    candidate_key,
    count_new_members,
    resolve_member,
)


def _m(name_hash: str, *, pk_id: str | None = None, is_cf: bool = False, **extra):
    """A detached Member carrying just the attrs dedup reads.

    The encrypted name is bound to the pre-allocated id, the same shape every
    importer produces for real candidates, so _apply_update's re-bind path
    runs against realistic rows.
    """
    mid = uuid.uuid4()
    return Member(
        id=mid,
        name=encrypt(name_hash, aad=member_name_aad(mid)),
        name_hash=name_hash,
        pluralkit_id=pk_id,
        is_custom_front=is_cf,
        **extra,
    )


def _system(*, safeguarded: bool):
    """Just the attributes `is_exposure_safeguarded` and the query read."""
    return SimpleNamespace(
        id=uuid.uuid4(),
        safety_grace_period_days=7 if safeguarded else 0,
        safety_applies_to_profile_visibility=safeguarded,
    )


class _StubSession:
    """Stands in for the session the exposure query runs on.

    Every execute() returns `rows`, which is all `shared_view_memberships`
    reads, and `queries` counts the round trips so a test can assert the
    gate short-circuits before touching the DB at all.
    """

    def __init__(self, rows=()):
        self._rows = list(rows)
        self.queries = 0

    async def execute(self, _stmt):
        self.queries += 1
        rows = self._rows
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))


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


async def test_create_strategy_always_creates_even_on_match():
    idx = MemberMatchIndex()
    idx.register(_m("dup"))
    cand = _m("dup")
    res = await resolve_member(
        cand,
        index=idx,
        strategy=ImportConflictStrategy.CREATE,
        db=_StubSession(),
        system=_system(safeguarded=True),
    )
    assert res.disposition == "created"
    assert res.member is cand


async def test_skip_returns_existing_untouched():
    existing = _m("dup", display_name="keep")
    idx = MemberMatchIndex()
    idx.register(existing)
    cand = _m("dup", display_name="ignored")
    res = await resolve_member(
        cand,
        index=idx,
        strategy=ImportConflictStrategy.SKIP,
        db=_StubSession(),
        system=_system(safeguarded=True),
    )
    assert res.disposition == "skipped"
    assert res.member is existing
    assert existing.display_name == "keep"


async def test_update_overwrites_set_fields_preserves_unset():
    existing = _m("dup", display_name="old", pronouns="they/them", emoji=None)
    idx = MemberMatchIndex()
    idx.register(existing)
    cand = _m("dup", display_name="new", pronouns=None, emoji="star")
    res = await resolve_member(
        cand,
        index=idx,
        strategy=ImportConflictStrategy.UPDATE,
        db=_StubSession(),
        system=_system(safeguarded=True),
    )
    assert res.disposition == "updated"
    assert res.member is existing
    assert existing.display_name == "new"      # candidate had a value -> overwrite
    assert existing.pronouns == "they/them"    # candidate None -> preserved
    assert existing.emoji == "star"            # candidate set -> filled in
    # The candidate's encrypted name was re-bound to the existing row's AAD,
    # not ciphertext-copied: it must decrypt under the existing id.
    assert decrypt(existing.name, aad=member_name_aad(existing.id)) == "dup"


async def test_update_leaves_privacy_alone_when_candidate_has_none():
    """Formats with no privacy model must not null out the existing setting."""
    existing = _m("dup", privacy=PrivacyLevel.PUBLIC)
    idx = MemberMatchIndex()
    idx.register(existing)
    res = await resolve_member(
        _m("dup"),
        index=idx,
        strategy=ImportConflictStrategy.UPDATE,
        db=_StubSession(),
        system=_system(safeguarded=True),
    )
    assert res.disposition == "updated"
    assert existing.privacy == PrivacyLevel.PUBLIC


async def test_no_match_creates_and_registers_for_intra_batch():
    idx = MemberMatchIndex()
    db, system = _StubSession(), _system(safeguarded=True)
    first = _m("dup")
    r1 = await resolve_member(
        first,
        index=idx,
        strategy=ImportConflictStrategy.SKIP,
        db=db,
        system=system,
    )
    assert r1.disposition == "created"
    # A later row in the same batch with the same key dedups against the
    # one just created, not a fresh insert.
    second = _m("dup")
    r2 = await resolve_member(
        second,
        index=idx,
        strategy=ImportConflictStrategy.SKIP,
        db=db,
        system=system,
    )
    assert r2.disposition == "skipped"
    assert r2.member is first


async def test_member_and_custom_front_same_name_both_created():
    idx = MemberMatchIndex()
    db, system = _StubSession(), _system(safeguarded=True)
    member = _m("alex", is_cf=False)
    cf = _m("alex", is_cf=True)
    r1 = await resolve_member(
        member,
        index=idx,
        strategy=ImportConflictStrategy.SKIP,
        db=db,
        system=system,
    )
    r2 = await resolve_member(
        cf, index=idx, strategy=ImportConflictStrategy.SKIP, db=db, system=system
    )
    assert r1.disposition == "created"
    assert r2.disposition == "created"  # different scope, no false match


# --- the privacy raise gate ------------------------------------------------
#
# An import job has no step-up channel, so UPDATE must not publish a member
# that PATCH /v1/members would only publish behind re-auth plus a grace
# window. Every other direction stays free.


async def _update(existing, candidate, *, db, system):
    idx = MemberMatchIndex()
    idx.register(existing)
    return await resolve_member(
        candidate,
        index=idx,
        strategy=ImportConflictStrategy.UPDATE,
        db=db,
        system=system,
    )


async def test_update_holds_privacy_raise_that_would_publish():
    existing = _m("ren", privacy=PrivacyLevel.PRIVATE, display_name="old")
    db = _StubSession(rows=[object()])  # sits in a view a live grant points at
    res = await _update(
        existing,
        _m("ren", privacy=PrivacyLevel.PUBLIC, display_name="new"),
        db=db,
        system=_system(safeguarded=True),
    )
    assert res.disposition == "updated"
    assert existing.privacy == PrivacyLevel.PRIVATE     # the raise was withheld
    assert existing.display_name == "new"               # everything else applied
    # Named in the report so a withheld flip is never silent.
    assert res.privacy_held_name == "ren"


async def test_update_applies_privacy_raise_when_no_grant_points_at_them():
    existing = _m("ren", privacy=PrivacyLevel.PRIVATE)
    db = _StubSession(rows=[])  # in no view anything points at
    res = await _update(
        existing,
        _m("ren", privacy=PrivacyLevel.PUBLIC),
        db=db,
        system=_system(safeguarded=True),
    )
    assert existing.privacy == PrivacyLevel.PUBLIC
    assert res.privacy_held_name is None


async def test_update_applies_privacy_raise_when_safety_is_not_armed():
    existing = _m("ren", privacy=PrivacyLevel.PRIVATE)
    db = _StubSession(rows=[object()])
    res = await _update(
        existing,
        _m("ren", privacy=PrivacyLevel.PUBLIC),
        db=db,
        system=_system(safeguarded=False),
    )
    assert existing.privacy == PrivacyLevel.PUBLIC
    assert res.privacy_held_name is None
    assert db.queries == 0  # short-circuits before the query


async def test_update_never_gates_lowering():
    existing = _m("ren", privacy=PrivacyLevel.PUBLIC)
    db = _StubSession(rows=[object()])
    for lower in (PrivacyLevel.FRIENDS, PrivacyLevel.PRIVATE):
        existing.privacy = PrivacyLevel.PUBLIC
        res = await _update(
            existing,
            _m("ren", privacy=lower),
            db=db,
            system=_system(safeguarded=True),
        )
        assert existing.privacy == lower
        assert res.privacy_held_name is None
    assert db.queries == 0  # un-exposing, so the gate never asks


async def test_update_does_not_gate_an_already_public_member():
    existing = _m("ren", privacy=PrivacyLevel.PUBLIC)
    db = _StubSession(rows=[object()])
    res = await _update(
        existing,
        _m("ren", privacy=PrivacyLevel.PUBLIC),
        db=db,
        system=_system(safeguarded=True),
    )
    assert existing.privacy == PrivacyLevel.PUBLIC
    assert res.privacy_held_name is None
    assert db.queries == 0  # nothing moves, so nothing to check


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
