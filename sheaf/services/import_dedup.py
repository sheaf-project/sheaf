"""Member deduplication for re-imports.

Every importer used to append members blindly, so importing the same
export twice doubled the roster. This module adds a match-and-resolve
layer: an importer builds its candidate Member exactly as before, then
asks `resolve_member()` what to do with it given the chosen strategy and
the members already in the system.

Match key: the source's stable id where both the candidate and an
existing member carry one (`pluralkit_id`), otherwise the name
blind-index (`name_hash`) scoped by `is_custom_front`. Names are not
guaranteed unique within a system, so the name-hash path is best-effort:
a system that genuinely has two members sharing a name will match the
first. `pluralkit_id` is exact, so PK re-imports round-trip cleanly.

The name-hash scope matters because some formats (SimplyPlural,
PluralSpace, Prism) store custom fronts as Member rows with
`is_custom_front=True`. Without the scope, a member and a custom front
that happen to share a name would match, and UPDATE would flip
`is_custom_front` and corrupt the member. `pluralkit_id` is member-only
(custom fronts never carry one), so that path needs no scoping.

Strategies:
- CREATE: always insert (the pre-dedup behaviour).
- SKIP (default): an existing match is left untouched; the candidate is
  not added.
- UPDATE: an existing match's importable fields are overwritten from the
  candidate.

The caller is responsible for two things based on the disposition:
  * db.add() the candidate ONLY when disposition == "created";
  * use the returned member in its source-id -> member map either way,
    so downstream sections (fronts, groups, custom fields) link to the
    right row whether it was created, skipped, or updated.
"""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.crypto import decrypt, encrypt
from sheaf.encrypted_fields import (
    member_description_aad,
    member_name_aad,
    member_note_aad,
)
from sheaf.models.member import Member


class ImportConflictStrategy(enum.StrEnum):
    CREATE = "create"
    SKIP = "skip"
    UPDATE = "update"


# Plaintext fields every importer always sets on a new Member, so UPDATE
# always overwrites them. is_custom_front is deliberately NOT here: matching
# is already scoped by it (a member only matches a member, a custom front
# only a custom front), so a match always agrees, and some importers
# leave it None on the candidate (relying on the column server-default),
# which would null out the existing row's NOT NULL column. The encrypted
# `name` is handled separately (see `_ENCRYPTED_ALWAYS`); `name_hash` is a
# blind index (not encrypted) and is copied verbatim.
_ALWAYS_OVERWRITE = ("name_hash", "privacy")
# Optional plaintext fields: UPDATE overwrites only when the candidate
# carries a value, so a re-import never nulls a field the source format
# doesn't model (e.g. PluralKit has no emoji, so a PK update must not wipe
# an emoji the user set after the first import).
_OVERWRITE_IF_SET = (
    "display_name",
    "pronouns",
    "avatar_url",
    "banner_url",
    "color",
    "birthday",
    "pluralkit_id",
    "emoji",
)
# Encrypted fields: their ciphertext is AAD-bound to the owning row's id, so
# an UPDATE cannot copy the candidate's ciphertext onto the existing row -
# it would stay bound to the candidate's id and become undecryptable on the
# existing row. Each is decrypted under the candidate's AAD and re-encrypted
# under the existing row's AAD instead (a legitimate cross-row move). `name`
# is always re-bound; `description`/`note` only when the candidate carries a
# value, mirroring the plaintext always/if-set split.
_ENCRYPTED_ALWAYS = {"name": member_name_aad}
_ENCRYPTED_IF_SET = {
    "description": member_description_aad,
    "note": member_note_aad,
}


@dataclass
class MemberMatchIndex:
    """In-memory index of a system's existing members, by match key.

    The name-hash index is keyed by `(is_custom_front, name_hash)` so a
    member and a custom front sharing a name don't match each other.
    """

    by_pk_id: dict[str, Member] = field(default_factory=dict)
    by_name_hash: dict[tuple[bool, str], Member] = field(default_factory=dict)

    def find(
        self,
        *,
        name_hash: str,
        is_custom_front: bool,
        pluralkit_id: str | None = None,
    ) -> Member | None:
        if pluralkit_id and pluralkit_id in self.by_pk_id:
            return self.by_pk_id[pluralkit_id]
        return self.by_name_hash.get((bool(is_custom_front), name_hash))

    def register(self, member: Member) -> None:
        """Record a member so later candidates dedup against it too.

        First-wins on collisions: the earliest existing (or earliest
        created-this-run) member is the canonical target.
        """
        if member.pluralkit_id:
            self.by_pk_id.setdefault(member.pluralkit_id, member)
        if member.name_hash:
            self.by_name_hash.setdefault(
                (bool(member.is_custom_front), member.name_hash), member
            )


async def load_member_match_index(
    db: AsyncSession, system_id: uuid.UUID
) -> MemberMatchIndex:
    """Build the match index from the members already in the system."""
    rows = await db.execute(select(Member).where(Member.system_id == system_id))
    index = MemberMatchIndex()
    for m in rows.scalars().all():
        index.register(m)
    return index


@dataclass
class Resolution:
    member: Member
    disposition: str  # "created" | "skipped" | "updated"


def _rebind(ciphertext: str, src_aad: bytes, dst_aad: bytes) -> str:
    """Move an encrypted value between rows: decrypt under the source row's
    AAD, re-encrypt under the destination's. A v1 candidate ciphertext still
    decrypts (its AAD is ignored), so old rows re-bind cleanly and land on v2.
    """
    return encrypt(decrypt(ciphertext, aad=src_aad), aad=dst_aad)


def _apply_update(existing: Member, candidate: Member) -> None:
    for fld in _ALWAYS_OVERWRITE:
        setattr(existing, fld, getattr(candidate, fld))
    for fld in _OVERWRITE_IF_SET:
        val = getattr(candidate, fld, None)
        if val is not None:
            setattr(existing, fld, val)
    # Encrypted fields: re-bind the ciphertext from the candidate's AAD to the
    # existing row's AAD rather than copying it (see the field-list comments).
    for fld, aad_for in _ENCRYPTED_ALWAYS.items():
        setattr(
            existing,
            fld,
            _rebind(
                getattr(candidate, fld),
                aad_for(candidate.id),
                aad_for(existing.id),
            ),
        )
    for fld, aad_for in _ENCRYPTED_IF_SET.items():
        val = getattr(candidate, fld, None)
        if val is not None:
            setattr(
                existing,
                fld,
                _rebind(val, aad_for(candidate.id), aad_for(existing.id)),
            )


def resolve_member(
    candidate: Member,
    *,
    index: MemberMatchIndex,
    strategy: ImportConflictStrategy,
) -> Resolution:
    """Decide how a freshly-built candidate relates to existing members.

    On "created" the candidate is registered in the index so a later
    intra-import row with the same key dedups against it too.
    """
    if strategy == ImportConflictStrategy.CREATE:
        return Resolution(candidate, "created")
    existing = index.find(
        name_hash=candidate.name_hash,
        is_custom_front=bool(candidate.is_custom_front),
        pluralkit_id=candidate.pluralkit_id,
    )
    if existing is None:
        index.register(candidate)
        return Resolution(candidate, "created")
    if strategy == ImportConflictStrategy.SKIP:
        return Resolution(existing, "skipped")
    _apply_update(existing, candidate)
    return Resolution(existing, "updated")


def candidate_key(member: Member) -> tuple[str, str | None, bool]:
    """The (name_hash, pluralkit_id, is_custom_front) match key for a
    freshly-built candidate, as `count_new_members` expects it."""
    return (member.name_hash, member.pluralkit_id, bool(member.is_custom_front))


def count_new_members(
    keys: list[tuple[str, str | None, bool]],
    *,
    index: MemberMatchIndex,
    strategy: ImportConflictStrategy,
) -> int:
    """Count how many (name_hash, pluralkit_id, is_custom_front) candidate
    keys would be created rather than skipped/updated.

    Used to size the tier member-cap check: under SKIP/UPDATE a pure
    re-import of members already in the system adds nothing, so it must
    not trip the cap. Mirrors `resolve_member`'s matching (including the
    intra-batch dedup of earlier new keys) without building Member rows.
    """
    if strategy == ImportConflictStrategy.CREATE:
        return len(keys)
    seen_new_pk: set[str] = set()
    seen_new_name: set[tuple[bool, str]] = set()
    new_count = 0
    for name_hash, pk_id, is_cf in keys:
        if pk_id and (pk_id in index.by_pk_id or pk_id in seen_new_pk):
            continue
        name_key = (bool(is_cf), name_hash)
        if not pk_id and (
            name_key in index.by_name_hash or name_key in seen_new_name
        ):
            continue
        new_count += 1
        if pk_id:
            seen_new_pk.add(pk_id)
        else:
            seen_new_name.add(name_key)
    return new_count
