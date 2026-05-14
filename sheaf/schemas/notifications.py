"""Pydantic schemas for the front-change notifications API.

Distinct request/response models per endpoint family. Names follow the
existing pattern (Create, Update, Read).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator

# ---- watch tokens ---------------------------------------------------------


class WatchTokenCreate(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class WatchTokenUpdate(BaseModel):
    label: str | None = Field(default=None, max_length=120)


class WatchTokenRevokeConfirm(BaseModel):
    password: str | None = None
    totp_code: str | None = None


class WatchTokenRead(BaseModel):
    id: uuid.UUID
    system_id: uuid.UUID
    label: str | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime
    channel_count: int = 0

    model_config = {"from_attributes": True}


# ---- notification channels ------------------------------------------------


_DESTINATION_TYPES = Literal[
    "web_push",
    "webhook",
    "ntfy",
    "pushover",
    "mobile_push",
    # Legacy mobile types retained so read-back of any historical row
    # (export, audit) still validates. Channel creation refuses them;
    # a migration collapsed any existing channel rows to mobile_push.
    "fcm",
    "apns_dev",
    "apns_prod",
]
_PAYLOAD_SENSITIVITIES = Literal["full", "minimal", "bare"]
_COFRONT_REDACTIONS = Literal["count", "someone", "suppress"]


class GroupRuleSpec(BaseModel):
    group_id: uuid.UUID
    rule: Literal["include", "exclude"]
    include_private: Literal["inherit", "yes", "no"] = "inherit"


class MemberRuleSpec(BaseModel):
    member_id: uuid.UUID
    rule: Literal["include", "exclude"]


class QuietHours(BaseModel):
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    tz: str = "UTC"  # IANA name, e.g. "Europe/Berlin"

    @field_validator("tz")
    @classmethod
    def _validate_tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {v!r}") from exc
        return v


class ChannelCreate(BaseModel):
    name: str = Field(..., max_length=120)
    destination_type: _DESTINATION_TYPES
    # For webhook/ntfy/pushover the owner must provide config now (no
    # activation flow). For web_push, leave empty: recipient supplies it
    # at redemption time.
    destination_config: dict[str, Any] = Field(default_factory=dict)
    # Webhook secret (cleartext). Stored encrypted; only echoed back to the
    # owner once (never on subsequent reads).
    webhook_secret: str | None = None

    base_all_members: bool = False
    base_include_private: bool = False
    trigger_on_start: bool = True
    trigger_on_stop: bool = False
    trigger_on_cofront_change: bool = False
    cofront_redaction: _COFRONT_REDACTIONS = "count"
    payload_sensitivity: _PAYLOAD_SENSITIVITIES = "full"
    debounce_seconds: int = Field(default=30, ge=0, le=86400)
    aggregation_window_seconds: int = Field(default=0, ge=0, le=86400)
    quiet_hours: QuietHours | None = None

    group_rules: list[GroupRuleSpec] = Field(default_factory=list)
    member_rules: list[MemberRuleSpec] = Field(default_factory=list)


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    destination_config: dict[str, Any] | None = None
    webhook_secret: str | None = None
    base_all_members: bool | None = None
    base_include_private: bool | None = None
    trigger_on_start: bool | None = None
    trigger_on_stop: bool | None = None
    trigger_on_cofront_change: bool | None = None
    cofront_redaction: _COFRONT_REDACTIONS | None = None
    payload_sensitivity: _PAYLOAD_SENSITIVITIES | None = None
    debounce_seconds: int | None = Field(default=None, ge=0, le=86400)
    aggregation_window_seconds: int | None = Field(default=None, ge=0, le=86400)
    quiet_hours: QuietHours | None = None
    # Replacement semantics: if provided, replaces the entire L2/L3 set.
    group_rules: list[GroupRuleSpec] | None = None
    member_rules: list[MemberRuleSpec] | None = None

    # NOT-NULL columns on the model; `| None` is only here so
    # model_fields_set can distinguish omitted vs supplied. quiet_hours
    # is genuinely nullable (null = clear the quiet-hours window), so
    # it's not in this list.
    @field_validator(
        "name",
        "destination_config",
        "base_all_members",
        "base_include_private",
        "trigger_on_start",
        "trigger_on_stop",
        "trigger_on_cofront_change",
        "cofront_redaction",
        "payload_sensitivity",
        "debounce_seconds",
        "aggregation_window_seconds",
    )
    @classmethod
    def _reject_explicit_null(cls, v):
        if v is None:
            raise ValueError("cannot be null")
        return v


class ChannelRead(BaseModel):
    id: uuid.UUID
    watch_token_id: uuid.UUID
    name: str
    destination_type: str
    destination_state: str
    # True when destination_state == 'disabled' was caused by the owner
    # pausing the channel (not the recipient unsubscribing). Lets the
    # recipient UI render "Paused by sender" instead of "Unsubscribed".
    paused_by_sender: bool = False
    # destination_config is echoed back for non-secret types (ntfy server URL,
    # webhook URL minus secret, pushover user key). Secrets never leak here.
    destination_config: dict[str, Any]
    event_type: str
    activation_code_expires_at: datetime | None
    redeemed_at: datetime | None
    redeemed_by_account_id: uuid.UUID | None
    base_all_members: bool
    base_include_private: bool
    trigger_on_start: bool
    trigger_on_stop: bool
    trigger_on_cofront_change: bool
    cofront_redaction: str
    payload_sensitivity: str
    debounce_seconds: int
    aggregation_window_seconds: int
    quiet_hours: dict[str, Any] | None
    group_rules: list[GroupRuleSpec] = Field(default_factory=list)
    member_rules: list[MemberRuleSpec] = Field(default_factory=list)
    last_delivered_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ChannelDeleteConfirm(BaseModel):
    password: str | None = None
    totp_code: str | None = None


class ChannelCreateResponse(BaseModel):
    """For push-style destinations the owner gets a one-time activation URL.
    For webhook/ntfy/pushover, `activation_url` is None and channel is
    `active` immediately."""

    channel: ChannelRead
    activation_url: str | None = None
    activation_expires_at: datetime | None = None


class ReissueActivationResponse(BaseModel):
    activation_url: str
    activation_expires_at: datetime


class PreviewMember(BaseModel):
    member_id: uuid.UUID
    name: str
    is_private: bool
    attribution: str


class PreviewResponse(BaseModel):
    included: list[PreviewMember]
    excluded: list[PreviewMember]
    warnings: list[str] = Field(default_factory=list)


class TestDispatchResponse(BaseModel):
    delivered: bool
    error: str | None = None


# ---- recipient-facing -----------------------------------------------------


class PushSubscription(BaseModel):
    endpoint: str
    keys: dict[str, str]


class RedeemRequest(BaseModel):
    activation_code: str
    push_subscription: PushSubscription | None = None


class RedeemResponse(BaseModel):
    # Empty for mobile-push channels (FCM/APNS_DEV/APNS_PROD), which
    # have no anonymous-capability /manage URL — recipients manage in
    # the app via /v1/notifications/receiving/{channel_id}/unsubscribe.
    management_url: str
    channel_name: str
    system_label: str | None = None


class RedeemPreview(BaseModel):
    """Read-only summary returned by `/notifications/redeem-preview`.

    Lets the recipient-facing redeem UI branch on destination type
    before doing anything destructive (registering a service worker,
    asking for notification permission, etc.). Reveals strictly less
    than redemption — anyone who already has the code can redeem; the
    preview just tells them what to render."""

    destination_type: str
    channel_name: str
    system_label: str | None = None
    expires_at: datetime | None = None


class ManageChannelView(BaseModel):
    channel_id: uuid.UUID
    channel_name: str
    system_label: str | None = None
    destination_type: str
    destination_state: str
    paused_by_sender: bool = False


class ReceivingChannelView(BaseModel):
    """An account-bound channel from the recipient's perspective.

    Returned by `GET /v1/notifications/receiving` and lists every channel
    where `redeemed_by_account_id` matches the authenticated user. Lets the
    recipient see and manage subscriptions across systems without juggling
    capability URLs.
    """

    channel_id: uuid.UUID
    channel_name: str
    system_label: str | None = None
    destination_type: str
    destination_state: str
    paused_by_sender: bool = False
    redeemed_at: datetime | None
    last_delivered_at: datetime | None
