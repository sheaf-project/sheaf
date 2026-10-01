import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PasskeyRegisterBegin(BaseModel):
    # Enrolling a passkey mints durable access that skips TOTP, so it is held
    # to a stronger standard than enabling TOTP itself: the password, plus a
    # code whenever the account has TOTP. A stolen live session with a
    # shoulder-surfed password must not be able to enrol its own key.
    password: str
    totp_code: str | None = None


class PasskeyRegisterBeginResponse(BaseModel):
    # The exact object `navigator.credentials.create({publicKey})` takes,
    # already in the JSON encoding the browser's `parseCreationOptionsFromJSON`
    # (or the equivalent hand decoding) expects. Opaque to the client.
    options: dict


class PasskeyRegisterComplete(BaseModel):
    # `PublicKeyCredential.toJSON()` from the browser, verbatim.
    credential: dict
    nickname: str | None = Field(default=None, max_length=128)


class PasskeyUpdate(BaseModel):
    nickname: str | None = Field(default=None, max_length=128)


class PasskeyRead(BaseModel):
    id: uuid.UUID
    nickname: str | None
    # The relying-party ID this credential was created under, and whether it
    # matches the instance's current one. After a domain move the old ones
    # stay listed with `usable` false, so the owner can see why the key no
    # longer offers itself and re-enrol, instead of it failing undiagnosably.
    rp_id: str
    usable: bool
    transports: list[str]
    aaguid: uuid.UUID | None
    # A synced passkey (phone keychain, password manager) versus a single
    # hardware key. Shown so a user can tell which is which in the list.
    backup_eligible: bool
    backup_state: bool
    created_at: datetime
    last_used_at: datetime | None

    model_config = {"from_attributes": True}
