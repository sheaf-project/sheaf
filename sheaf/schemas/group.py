import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from sheaf.models.system import PrivacyLevel


class GroupCreate(BaseModel):
    name: str = Field(max_length=100)
    description: str | None = None
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


class GroupUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    description: str | None = None
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
