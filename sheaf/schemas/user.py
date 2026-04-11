import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    invite_code: str | None = None
    newsletter_opt_in: bool = False


class UserLogin(BaseModel):
    email: EmailStr
    password: str
    totp_code: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


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
    newsletter_opt_in: bool = False
    email_delivery_status: str = "ok"
    email_revalidation_required: bool = False

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    newsletter_opt_in: bool | None = None


class TOTPSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class TOTPVerify(BaseModel):
    code: str = Field(min_length=6, max_length=6)
