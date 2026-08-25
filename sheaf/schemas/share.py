"""Owner-side schemas for share views and grants.

Note what never appears in any response here: `token_hash`. A link token is a
bearer capability that exists in plaintext exactly once, in the response to the
call that created or rotated it (`ShareGrantCreated.token`). Nothing can read it
back afterwards, including the owner.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from sheaf.models.share import ShareSubjectType
from sheaf.schemas.public_profile import (
    PublicFrontingView,
    PublicGroupsView,
    PublicMemberView,
    PublicRelationshipsView,
    PublicSystemView,
)

# Re-auth fields shared by every action that EXPOSES something. They are only
# consulted when the action is actually deferred (grace window armed for the
# profile_visibility category); otherwise they are ignored.
_PASSWORD = Field(default=None, description="Required when the change is deferred")


class ShareViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # The roster is what a view is FOR, so it defaults on; everything that
    # widens the page beyond it defaults off.
    include_members: bool = True
    include_bio: bool = False
    include_fronting: bool = False
    fronting_show_count: bool = True
    include_relationships: bool = False
    include_groups: bool = False
    # Not an exposure flag (see services/sharing.EXPOSURE_FLAGS): it addresses
    # members the roster already shows rather than showing anyone new.
    member_permalinks: bool = False


class ShareViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    include_members: bool | None = None
    include_bio: bool | None = None
    include_fronting: bool | None = None
    fronting_show_count: bool | None = None
    include_relationships: bool | None = None
    include_groups: bool | None = None
    member_permalinks: bool | None = None

    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareViewMemberRead(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    status: str
    activates_at: datetime | None = None
    # Which group expansion put this member here, or null for a hand-picked one
    # (and for a row whose group has since been deleted). Owner-side only, and
    # it is what detaching that group will remove - so the client can be honest
    # about the consequence rather than guessing from the group's roster.
    added_via_group_id: uuid.UUID | None = None


class ShareViewFieldRead(BaseModel):
    id: uuid.UUID
    field_id: uuid.UUID
    status: str
    activates_at: datetime | None = None


class ShareViewGroupRead(BaseModel):
    id: uuid.UUID
    group_id: uuid.UUID
    synced_at: datetime


class ShareViewRead(BaseModel):
    id: uuid.UUID
    name: str
    include_members: bool
    include_bio: bool
    include_fronting: bool
    fronting_show_count: bool
    include_relationships: bool
    include_groups: bool
    # Instant in both directions and never staged, so it has no pending twin
    # below: it exposes no new data, only a stable address for members the
    # roster already shows.
    member_permalinks: bool
    created_at: datetime
    # True when any non-revoked grant points at this view, including one still
    # inside its grace window. Drives the "this view is live" indicator.
    is_shared: bool
    # A flag flip that EXPOSES more on an already-shared view is staged rather
    # than applied: the live flag above is still the truth, and these say what
    # it will become when `flags_activate_at` passes. Null = nothing staged.
    pending_include_bio: bool | None = None
    pending_include_fronting: bool | None = None
    pending_fronting_show_count: bool | None = None
    pending_include_relationships: bool | None = None
    pending_include_members: bool | None = None
    pending_include_groups: bool | None = None
    flags_activate_at: datetime | None = None
    members: list[ShareViewMemberRead]
    fields: list[ShareViewFieldRead]
    groups: list[ShareViewGroupRead]


class ShareViewMemberAdd(BaseModel):
    member_id: uuid.UUID
    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareViewFieldAdd(BaseModel):
    field_id: uuid.UUID
    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareViewGroupAdd(BaseModel):
    group_id: uuid.UUID
    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareViewGroupAddResult(BaseModel):
    """Result of expanding a group into a view.

    The skip counts are surfaced so the user is told plainly that some of the
    group did not get added, rather than silently getting a smaller view than
    they expected. `skipped_never_shareable` are secret members;
    `skipped_not_public` are members whose privacy (private/friends) keeps them
    off the public tier, so auto-inclusion left them out.
    """

    added: int
    skipped_never_shareable: int
    skipped_not_public: int


class ShareGrantCreate(BaseModel):
    view_id: uuid.UUID
    subject_type: ShareSubjectType
    note: str | None = Field(default=None, max_length=200)
    expires_at: datetime | None = None

    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareGrantRead(BaseModel):
    id: uuid.UUID
    view_id: uuid.UUID
    subject_type: str
    note: str | None
    status: str
    activates_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ShareGrantCreated(BaseModel):
    """A newly created or rotated grant.

    `token` is populated for link grants ONLY, and only here. It is not stored
    in retrievable form and cannot be shown again; the client must present it
    to the user immediately.
    """

    grant: ShareGrantRead
    token: str | None = None


class ShareAuditEntry(BaseModel):
    """One row of "who can currently see what"."""

    grant: ShareGrantRead
    view_id: uuid.UUID
    view_name: str
    member_count: int
    field_count: int
    # Whether the curated roster above is actually served. With this off the
    # counts still describe real curation, they are just not published - which
    # is why the audit reports the flag rather than zeroing the count and
    # letting the owner think their work vanished.
    include_members: bool
    include_bio: bool
    include_fronting: bool
    include_relationships: bool
    include_groups: bool
    member_permalinks: bool
    # Edges this view would actually serve right now, computed with the same
    # query the projection uses. Zero whenever include_relationships is off, and
    # zero when the flag is on but no edge clears both the per-edge `public`
    # level and the member ceiling - so the audit says what a visitor sees, not
    # what the flag permits.
    relationship_count: int
    # Groups this view would actually serve right now, through the projection's
    # own choke point for the same reason. Zero whenever include_groups is off.
    group_count: int


class ShareAudit(BaseModel):
    entries: list[ShareAuditEntry]
    # Why NOTHING below is being served right now, or None when the entries
    # describe live exposure. One of "system_private" (the system-level privacy
    # selector is not public) or "account_state" (the account is not in good
    # standing). Account-level, not per-grant, because it suppresses every grant
    # at once.
    #
    # This exists so an owner whose page 404s can tell the difference between a
    # switch they can flip and one they cannot, instead of debugging their own
    # grants. Deliberately coarse: the anonymous surface returns the same
    # uniform 404 for every one of these, and the specific account state is
    # something the owner was already told at login, so naming it here would
    # add a leakable value to buy nothing.
    profile_suppressed: str | None = None


class SharePreview(BaseModel):
    """One view, as a visitor would receive it.

    Every section is the SAME schema the anonymous router serves, produced by
    the same `share_projection` function, so this cannot describe a page the
    public surface would not actually render. Nothing is added: an owner-only
    field here would be an owner-only field that looks like part of the page.

    `null` on a section is this bundle's spelling of the anonymous surface's
    404. There, a view that does not serve its roster (or fronting, or
    relationships, or groups) makes that endpoint UNADDRESSABLE rather than
    empty, so no visitor can distinguish "not published" from "published and
    empty". Bundling them means a preview has to say which sections did not
    answer, and null is that: it is not an empty list, because an empty list is
    a thing a served section can legitimately be and the owner needs to see the
    difference between the two.
    """

    system: PublicSystemView
    members: list[PublicMemberView] | None = None
    fronting: PublicFrontingView | None = None
    relationships: PublicRelationshipsView | None = None
    groups: PublicGroupsView | None = None
    # Why this would not serve to anyone right now, or None. Same coarse values
    # and same helper as `ShareAudit.profile_suppressed`. A preview WITHOUT this
    # would be the most convincing lie the feature could tell: an owner whose
    # system is set to private would be shown a complete, healthy-looking page
    # for something the world is getting a 404 for. Deliberately does not blank
    # the sections - the answer to "what would visitors see" and the answer to
    # "is anyone getting it" are two different questions, and a preview that
    # went dark would hide the very content the owner opened it to check.
    suppressed: str | None = None
