import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = None
    newsletter_opt_in: bool = False
    captcha: str | None = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None
    captcha: str | None = None
    remember_device: bool = False
    # Optional friendly label for the trusted-device row when
    # `remember_device=true`. Empty / unset = the row shows as
    # unnamed in Settings -> Account until the user renames it
    # there. Capped at 128 chars at write time.
    device_nickname: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class SecondarySessionResponse(TokenResponse):
    """Tokens plus the session id, so the caller (typically a phone minting
    a wearable companion session) can track the child for later management."""

    session_id: str


class SecondarySessionRequest(BaseModel):
    """Optional metadata for a wearable/companion session.

    `client_name` mirrors the X-Sheaf-Client header semantics — it's what
    surfaces in /sessions UI as the device label.
    """

    client_name: str | None = None


class TokenRefresh(BaseModel):
    refresh_token: str | None = None


class UserRead(BaseModel):
    id: uuid.UUID
    email: str
    totp_enabled: bool
    is_admin: bool
    tier: str
    account_status: str
    email_verified: bool
    created_at: datetime
    last_login_at: datetime | None
    deletion_requested_at: datetime | None = None
    deletion_scheduled_for: datetime | None = None
    newsletter_opt_in: bool = False
    email_delivery_status: str = "ok"
    email_revalidation_required: bool = False
    # When the operator engages cf-shield (Cloudflare under-attack mode),
    # users with this flag set have their sessions invalidated so their
    # traffic does not unwittingly traverse the CDN. Always present in
    # the response; the frontend hides the toggle unless the instance
    # reports shield_mode feature_enabled=true via /v1/shield-mode/status.
    disable_cdn_during_ddos: bool = False
    # Effective permission — True if global uploads are on, the user is an
    # admin, or the user is individually allowlisted.
    uploads_allowed: bool = True
    # Same logic but for bio/description image embeds specifically. False
    # when bios are disabled at the instance level even if avatars are on.
    bio_uploads_allowed: bool = True
    # Instance policy for linking to external images (bio embeds + avatar URLs).
    # Not user-gated — it's a privacy/CSP setting that applies uniformly.
    external_images_allowed: bool = True
    # Whether this user may upload animated avatars (GIF / animated WebP).
    # When False, the upload endpoint flattens animated input to its first
    # frame. The frontend uses this to decide whether to offer a
    # "keep animation" path in the cropper.
    animated_uploads_allowed: bool = False

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    newsletter_opt_in: bool | None = None
    disable_cdn_during_ddos: bool | None = None


class TOTPSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class TOTPVerify(BaseModel):
    code: str = Field(min_length=6, max_length=6)
