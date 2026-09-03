import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from sheaf.files import normalize_description_urls
from sheaf.models.system import PrivacyLevel


class GroupCreate(BaseModel):
    name: str = Field(max_length=100)
    # Long-form markdown, capped for the same reason a member bio is: the
    # image/footnote parse is superlinear and runs on the event loop (write path
    # and public projection), so an unbounded description is a cheap DoS lever.
    # 20k chars is generous for a real group description. See schemas/member.py.
    description: str | None = Field(default=None, max_length=20000)
    color: str | None = Field(default=None, max_length=7)
    parent_id: uuid.UUID | None = None
    # Born private unless asked otherwise, and asking otherwise runs the same
    # gate the PATCH raise does (see api/v1/groups.create_group): creating a
    # group already public and raising an existing one to public are the same
    # exposure, so "delete it and add it back as public" must not be a way
    # round the slower door.
    privacy: PrivacyLevel = PrivacyLevel.PRIVATE
    # Step-up credentials, carried for the same reason `RelationshipEdgeCreate`
    # carries them: a create that skips straight to public goes through the
    # same door as a raise. Popped before persistence.
    password: str | None = Field(
        default=None, description="Required when the change is deferred"
    )
    totp_code: str | None = None

    # Group descriptions are markdown that renders image embeds, exactly like a
    # system description or a member bio, so they get the same normalisation
    # those have always had: our own refs canonicalised to /v1/files/{key} with
    # signed query params stripped, external refs validated (HTTPS, no internal
    # IPs) and dropped when the instance policy says so. This was missing here
    # while every sibling schema had it, which left group descriptions as the
    # one markdown field where an http:// or internal-IP image survived to the
    # renderer.
    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    # See GroupCreate.description for the cap rationale.
    description: str | None = Field(default=None, max_length=20000)
    color: str | None = Field(default=None, max_length=7)
    parent_id: uuid.UUID | None = None
    privacy: PrivacyLevel | None = None

    # Step-up credentials for a raise that is actually deferred. NOT group
    # columns: the handler pops them before anything iterates the update, the
    # same way the member and relationship-edge handlers do, so they can never
    # be persisted.
    password: str | None = Field(
        default=None, description="Required when the change is deferred"
    )
    totp_code: str | None = None

    @field_validator("name")
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v

    # Same normalisation as GroupCreate; see the note there.
    @field_validator("description", mode="before")
    @classmethod
    def _normalize_description(cls, v: str | None) -> str | None:
        return normalize_description_urls(v)


class GroupRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    name: str
    description: str | None
    color: str | None
    parent_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    privacy: PrivacyLevel
    # A raise waiting out the grace window: `privacy` above is still the truth,
    # and this says what it will become when `privacy_activates_at` passes.
    # Null = nothing staged.
    pending_privacy: PrivacyLevel | None = None
    privacy_activates_at: datetime | None = None
    # finalize_after timestamp if queued for delete; null otherwise.
    pending_delete_at: datetime | None = None

    model_config = {"from_attributes": True}


class GroupMemberUpdate(BaseModel):
    member_ids: list[uuid.UUID]
