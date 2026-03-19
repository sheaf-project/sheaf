import uuid
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


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
    tier: str
    created_at: datetime
    last_login_at: datetime | None

    model_config = {"from_attributes": True}


class TOTPSetupResponse(BaseModel):
    secret: str
    provisioning_uri: str
    recovery_codes: list[str]


class TOTPVerify(BaseModel):
    code: str = Field(min_length=6, max_length=6)
