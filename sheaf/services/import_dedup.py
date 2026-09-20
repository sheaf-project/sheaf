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

One field UPDATE cannot overwrite freely is `privacy`. Raising an
existing member to `public` PUBLISHES them if they already sit in a share
view a grant points at, and the members API only allows that flip behind
step-up re-auth plus a grace window. A job has no step-up channel, so an
import must not do what the API refuses: the raise is applied only when
it exposes nothing, and otherwise the existing value stands and the
member is referenced (by id, never by decrypted name) in the job report
(`Resolution.privacy_held_member_id`). The hold does NOT consult the
profile_visibility safety category - see `_privacy_raise_exposes` for why
a gate the same file can switch off is no gate.
Lowering is the un-exposing direction and stays ungated.

A CREATE used to need no gate at all, because a brand new member is in no
view. That stopped being true when a view could be set to serve every
member whose privacy is `public`: under one of those, a member created
public by a file is published the moment the import commits, with the
file having chosen it. So the same hold covers creates - see
`_all_public_view_would_publish`.

The caller is responsible for three things based on the disposition:
  * db.add() the candidate ONLY when disposition == "created";
  * use the returned member in its source-id -> member map either way,
    so downstream sections (fronts, groups, custom fields) link to the
    right row whether it was created, skipped, or updated;
  * count and report `privacy_held_member_id` when it is set.
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
from sheaf.models.system import PrivacyLevel, System
from sheaf.services.sharing import (
    member_privacy_raise_exposes,
    view_serves_all_public_members,
)


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
# blind index (not encrypted) and is copied verbatim. `privacy` is handled
# separately too, because raising it can publish the member (see
# `_privacy_raise_exposes`).
_ALWAYS_OVERWRITE = ("name_hash",)
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

    # Memoised answer to "does this system publish every public member right
    # now" (`view_serves_all_public_members`). It is a property of the system's
    # VIEWS, identical for every candidate in the file, so asking per member
    # would be one query per row of a roster that can run to hundreds. Cached on
    # the index because the index is already the per-job snapshot of exactly
    # this kind of fact, and it lives for exactly one import. None means "not
    # asked yet".
    #
    # Staleness cannot hurt: the flag only ever makes the importer hold a
    # privacy raise back, so a cached True holds a raise that may have become
    # safe (the owner turned the view off mid-import), which is the direction
    # this module is deliberately wrong in. A cached False cannot be wrong in
    # the other direction either - turning the flag ON is a loosening that goes
    # through step-up and a grace window of its own.
    all_public_view: bool | None = None

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
    # The member's id when the import declined to give them the `public` level
    # the file asked for - either UPDATE declining to raise a matched member, or
    # CREATE declining to insert a new one already public under a view that
    # serves every public member. Else None. Callers count it and reference
    # the member in the job
    # report so a withheld flip is never silent. Deliberately the id and NOT the
    # decrypted name: job events are stored as plaintext JSONB, while member
    # names live in encrypted columns, so a report must not downgrade a name
    # into the clear. The id is the user's own roster member and resolves to a
    # current name client-side (the same discipline the archive importer's
    # image-reference report already follows).
    privacy_held_member_id: uuid.UUID | None = None


def privacy_hold_warning(member_id: uuid.UUID) -> str:
    """The report line for a privacy raise an import declined to apply.

    References the member by id, not by name: this string lands in the job's
    plaintext event log, and the member's name is an encrypted column. The web
    report resolves the id to the member's current name from the user's own
    roster; the id also drops straight into the members-page URL.
    """
    return (
        f"Kept a member (id {member_id}) private - the file makes them public "
        "and a shared view would publish them straight away, so publishing "
        "them needs re-authentication. Change it from the members page if that "
        "is what you want."
    )


def _rebind(ciphertext: str, src_aad: bytes, dst_aad: bytes) -> str:
    """Move an encrypted value between rows: decrypt under the source row's
    AAD, re-encrypt under the destination's. A v1 candidate ciphertext still
    decrypts (its AAD is ignored), so old rows re-bind cleanly and land on v2.
    """
    return encrypt(decrypt(ciphertext, aad=src_aad), aad=dst_aad)


async def _privacy_raise_exposes(
    db: AsyncSession, system: System, existing: Member, candidate: Member
) -> bool:
    """Whether taking the candidate's privacy would publish the existing row.

    Only the raise to `public` can expose: lowering takes the member off the
    public surface and an equal value moves nothing. A raise on a member no live
    or pending grant can reach exposes nobody and stays ungated - the same
    "would this actually reveal somebody" question PATCH /v1/members asks before
    it demands step-up re-auth, and the same place the parked friends tier will
    need an audience-aware test.

    Deliberately NOT keyed on the profile_visibility safety category, and that
    is the difference from the API. Two reasons, either of which is sufficient:

    - An import is non-interactive. Where the API would demand a step-up it can
      neither perform one nor stage a pending raise, so the only honest answers
      are "publish from a file with no gate at all" or "hold". It holds.
    - The category flag is itself importable (it rides in `system.safety` in the
      very same payload), so a hold that consulted it could be switched off by
      the file that wants the raise. A gate an attacker's input can disarm is
      not a gate.

    So the hold applies whenever the raise would actually put the member in
    front of somebody: keep the existing lower level and report it, rather than
    silently publishing somebody from a file. That is the conservative reading,
    and the worst an import can do to visibility is leave it where it was.

    "Actually" is `member_privacy_raise_exposes`, the same function the API's
    step-up decision goes through, called rather than re-expressed. The comment
    in members.py says these two must not drift, and they cannot now: when the
    interactive gate learned that a view with `include_all_public_members`
    publishes a member with no membership row at all, this learned it in the
    same edit. Re-deriving it here from a membership-row lookup would have left
    the import path silently publishing from a file in exactly the case the
    owner-side path had just been taught to stop.
    """
    if (
        candidate.privacy != PrivacyLevel.PUBLIC
        or existing.privacy == PrivacyLevel.PUBLIC
    ):
        return False
    return await member_privacy_raise_exposes(db, system, existing.id)


async def _all_public_view_would_publish(
    db: AsyncSession, system: System, index: MemberMatchIndex
) -> bool:
    """Would a member CREATED public by this file be published on commit?

    The create-side twin of `_privacy_raise_exposes`, and it exists because
    `include_all_public_members` deleted the assumption the create path rested
    on. "A new member is in no view" was a complete answer while a view's
    roster was an allowlist; under a view that serves every public member there
    is no view to be in, and a file that says `privacy: public` publishes the
    member it just created, with no step-up and no grace window in front of it.

    Gated identically to the update hold and for the same two reasons: an
    import cannot perform a step-up, and the safety category rides in the same
    payload so consulting it would let the file disarm the gate. The answer is
    memoised on the match index - it is one fact about the system's views, the
    same for every row in the file.

    Creating the member is never refused, only their PUBLICITY: they land at
    the model default (private) and the job report names them, so the owner
    makes the publish decision on a screen instead of a file making it for
    them.
    """
    if index.all_public_view is None:
        index.all_public_view = await view_serves_all_public_members(db, system)
    return index.all_public_view


def _apply_update(
    existing: Member, candidate: Member, *, apply_privacy: bool
) -> None:
    for fld in _ALWAYS_OVERWRITE:
        setattr(existing, fld, getattr(candidate, fld))
    # Not every source format models privacy, so a candidate that carries no
    # value leaves the existing setting alone rather than nulling the column.
    if apply_privacy and candidate.privacy is not None:
        existing.privacy = candidate.privacy
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


async def _resolve_created(
    candidate: Member,
    *,
    index: MemberMatchIndex,
    db: AsyncSession,
    system: System,
) -> Resolution:
    """A candidate that is being inserted, with its publicity vetted.

    Both create paths (`CREATE` outright, and `SKIP`/`UPDATE` finding no match)
    land here so neither can be the cheap way past the other. The only thing
    vetted is `privacy == public`, and only against
    `_all_public_view_would_publish` - the member is genuinely in no view, so
    nothing else about them can be exposed by inserting them.

    The candidate is demoted in place rather than refused: the row still
    imports, with everything else the file said about it, and only the level
    the owner never confirmed is dropped to the model's own default.
    """
    if candidate.privacy != PrivacyLevel.PUBLIC:
        return Resolution(candidate, "created")
    if not await _all_public_view_would_publish(db, system, index):
        return Resolution(candidate, "created")
    candidate.privacy = PrivacyLevel.PRIVATE
    return Resolution(candidate, "created", privacy_held_member_id=candidate.id)


async def resolve_member(
    candidate: Member,
    *,
    index: MemberMatchIndex,
    strategy: ImportConflictStrategy,
    db: AsyncSession,
    system: System,
) -> Resolution:
    """Decide how a freshly-built candidate relates to existing members.

    On "created" the candidate is registered in the index so a later
    intra-import row with the same key dedups against it too. UPDATE hits the
    DB only when the file would raise a matched member to public, which is the
    one overwrite that can publish somebody - and a CREATE hits it only when the
    file wants the new member public, which under an all-public view is the
    same publish by a different door.
    """
    if strategy == ImportConflictStrategy.CREATE:
        return await _resolve_created(candidate, index=index, db=db, system=system)
    existing = index.find(
        name_hash=candidate.name_hash,
        is_custom_front=bool(candidate.is_custom_front),
        pluralkit_id=candidate.pluralkit_id,
    )
    if existing is None:
        index.register(candidate)
        return await _resolve_created(candidate, index=index, db=db, system=system)
    if strategy == ImportConflictStrategy.SKIP:
        return Resolution(existing, "skipped")
    # Lock and re-read the matched row before evaluating (and possibly applying)
    # a privacy raise. The match index is a snapshot taken at job start; between
    # then and now the owner may have taken this member private in a concurrent
    # request. Without the row lock we would evaluate exposure against - and then
    # overwrite - a stale privacy value, silently undoing that concurrent change.
    # Only a raise to public can expose, so the lock is scoped to exactly that
    # case (the same conditions _privacy_raise_exposes needs its DB read for);
    # other dispositions keep the no-extra-query fast path. FOR UPDATE both
    # refreshes existing.privacy and serialises us against the writer for the
    # rest of the transaction, so the subsequent _apply_update is safe.
    if (
        candidate.privacy == PrivacyLevel.PUBLIC
        and existing.privacy != PrivacyLevel.PUBLIC
    ):
        await db.refresh(existing, ["privacy"], with_for_update=True)
    exposes = await _privacy_raise_exposes(db, system, existing, candidate)
    _apply_update(existing, candidate, apply_privacy=not exposes)
    return Resolution(
        existing,
        "updated",
        # Reference the held member by id, never by decrypted name: this reaches
        # the plaintext job event log (see Resolution.privacy_held_member_id).
        privacy_held_member_id=existing.id if exposes else None,
    )


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
