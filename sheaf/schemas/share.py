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

# Re-auth fields shared by every action that EXPOSES something. They are only
# consulted when the action is actually deferred (grace window armed for the
# profile_visibility category); otherwise they are ignored.
_PASSWORD = Field(default=None, description="Required when the change is deferred")


class ShareViewCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    include_bio: bool = False
    include_fronting: bool = False
    fronting_show_count: bool = True
    include_relationships: bool = False


class ShareViewUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    include_bio: bool | None = None
    include_fronting: bool | None = None
    fronting_show_count: bool | None = None
    include_relationships: bool | None = None

    password: str | None = _PASSWORD
    totp_code: str | None = None


class ShareViewMemberRead(BaseModel):
    id: uuid.UUID
    member_id: uuid.UUID
    status: str
    activates_at: datetime | None = None


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
    include_bio: bool
    include_fronting: bool
    fronting_show_count: bool
    include_relationships: bool
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
    include_bio: bool
    include_fronting: bool
    include_relationships: bool
    # Edges this view would actually serve right now, computed with the same
    # query the projection uses. Zero whenever include_relationships is off, and
    # zero when the flag is on but no edge clears both the per-edge `public`
    # level and the member ceiling - so the audit says what a visitor sees, not
    # what the flag permits.
    relationship_count: int


class ShareAudit(BaseModel):
    entries: list[ShareAuditEntry]
