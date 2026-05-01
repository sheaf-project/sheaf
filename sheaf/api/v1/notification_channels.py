"""Notification channels (owner-side): CRUD + activation + duplicate +
send-test + live preview."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.config import settings
from sheaf.crypto import encrypt
from sheaf.database import get_db
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.notification_channel import (
    DestinationState,
    DestinationType,
    NotificationChannel,
)
from sheaf.models.notification_channel_group_rule import NotificationChannelGroupRule
from sheaf.models.notification_channel_member_rule import NotificationChannelMemberRule
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.schemas.notifications import (
    ChannelCreate,
    ChannelCreateResponse,
    ChannelRead,
    ChannelUpdate,
    GroupRuleSpec,
    MemberRuleSpec,
    PreviewMember,
    PreviewResponse,
    ReceivingChannelView,
    ReissueActivationResponse,
    TestDispatchResponse,
)
from sheaf.services.members import member_name_plaintext
from sheaf.services.notifications.activation import (
    activation_url as build_activation_url,
)
from sheaf.services.notifications.activation import (
    issue_activation_code,
)
from sheaf.services.notifications.handlers import deliver
from sheaf.services.notifications.resolution import (
    build_group_name_lookup,
    resolve_member_visibility,
)

router = APIRouter(prefix="", tags=["notifications"])


# ---------- helpers --------------------------------------------------------


_PUSH_TYPES = {DestinationType.WEB_PUSH.value}
_DIRECT_TYPES = {
    DestinationType.WEBHOOK.value,
    DestinationType.NTFY.value,
    DestinationType.PUSHOVER.value,
}
_RESERVED_TYPES = {
    DestinationType.EMAIL.value,
    DestinationType.APNS.value,
    DestinationType.FCM.value,
    DestinationType.DISCORD.value,
}


async def _system_for_user(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _load_owned_token(
    db: AsyncSession, user: User, token_id: uuid.UUID
) -> WatchToken:
    system = await _system_for_user(user, db)
    result = await db.execute(
        select(WatchToken).where(
            WatchToken.id == token_id, WatchToken.system_id == system.id
        )
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Watch token not found"
        )
    return token


async def _load_owned_channel(
    db: AsyncSession, user: User, channel_id: uuid.UUID
) -> NotificationChannel:
    system = await _system_for_user(user, db)
    result = await db.execute(
        select(NotificationChannel)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .where(
            NotificationChannel.id == channel_id, WatchToken.system_id == system.id
        )
        .options(
            selectinload(NotificationChannel.group_rules),
            selectinload(NotificationChannel.member_rules),
            selectinload(NotificationChannel.watch_token),
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    return channel


def _channel_to_read(channel: NotificationChannel) -> ChannelRead:
    return ChannelRead(
        id=channel.id,
        watch_token_id=channel.watch_token_id,
        name=channel.name,
        destination_type=channel.destination_type,
        destination_state=channel.destination_state,
        destination_config=_redacted_destination_config(channel),
        event_type=channel.event_type,
        activation_code_expires_at=channel.activation_code_expires_at,
        redeemed_at=channel.redeemed_at,
        redeemed_by_account_id=channel.redeemed_by_account_id,
        base_all_members=channel.base_all_members,
        base_include_private=channel.base_include_private,
        trigger_on_start=channel.trigger_on_start,
        trigger_on_stop=channel.trigger_on_stop,
        trigger_on_cofront_change=channel.trigger_on_cofront_change,
        cofront_redaction=channel.cofront_redaction,
        payload_sensitivity=channel.payload_sensitivity,
        debounce_seconds=channel.debounce_seconds,
        aggregation_window_seconds=channel.aggregation_window_seconds,
        quiet_hours=channel.quiet_hours,
        group_rules=[
            GroupRuleSpec(
                group_id=r.group_id, rule=r.rule, include_private=r.include_private
            )
            for r in channel.group_rules
        ],
        member_rules=[
            MemberRuleSpec(member_id=r.member_id, rule=r.rule)
            for r in channel.member_rules
        ],
        last_delivered_at=channel.last_delivered_at,
        created_at=channel.created_at,
        updated_at=channel.updated_at,
    )


def _redacted_destination_config(channel: NotificationChannel) -> dict:
    """Strip secrets before echoing destination_config back to the client.

    Webhook secret lives in `webhook_secret_encrypted`, not in
    destination_config, so as long as we don't surface that, this is the
    full identifier set the client legitimately needs.
    """
    return dict(channel.destination_config or {})


def _validate_destination(body_type: str) -> None:
    if body_type in _RESERVED_TYPES:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"destination_type {body_type!r} not yet supported",
        )
    if body_type not in _PUSH_TYPES and body_type not in _DIRECT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"unknown destination_type {body_type!r}",
        )


def _validate_direct_config(body_type: str, config: dict) -> None:
    """Direct types (webhook/ntfy/pushover) require the owner to provide
    enough config to dispatch immediately."""
    if body_type == DestinationType.WEBHOOK.value and not config.get("url"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook destination requires destination_config.url",
        )
    if body_type == DestinationType.NTFY.value and (
        not config.get("server_url") or not config.get("topic")
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ntfy destination requires server_url and topic",
        )
    if body_type == DestinationType.PUSHOVER.value and not config.get("user_key"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="pushover destination requires user_key",
        )


# ---------- create / list / read / update / delete -------------------------


@router.post(
    "/watch-tokens/{token_id}/channels",
    response_model=ChannelCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def create_channel(
    token_id: uuid.UUID,
    body: ChannelCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelCreateResponse:
    token = await _load_owned_token(db, user, token_id)
    if token.revoked_at is not None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Watch token is revoked",
        )

    _validate_destination(body.destination_type)
    if body.destination_type in _DIRECT_TYPES:
        _validate_direct_config(body.destination_type, body.destination_config)

    channel = NotificationChannel(
        id=uuid.uuid4(),
        watch_token_id=token.id,
        name=body.name,
        destination_type=body.destination_type,
        destination_config=body.destination_config or {},
        base_all_members=body.base_all_members,
        base_include_private=body.base_include_private,
        trigger_on_start=body.trigger_on_start,
        trigger_on_stop=body.trigger_on_stop,
        trigger_on_cofront_change=body.trigger_on_cofront_change,
        cofront_redaction=body.cofront_redaction,
        payload_sensitivity=body.payload_sensitivity,
        debounce_seconds=body.debounce_seconds,
        aggregation_window_seconds=body.aggregation_window_seconds,
        quiet_hours=body.quiet_hours.model_dump() if body.quiet_hours else None,
    )

    if body.destination_type == DestinationType.WEBHOOK.value and body.webhook_secret:
        channel.webhook_secret_encrypted = encrypt(body.webhook_secret)

    activation_url = None
    activation_expires = None
    if body.destination_type in _PUSH_TYPES:
        # Push-style: pending registration until recipient redeems.
        issued = issue_activation_code(ttl_days=settings.activation_code_ttl_days)
        channel.destination_state = DestinationState.PENDING_REGISTRATION.value
        channel.activation_code_hash = issued.code_hash
        channel.activation_code_expires_at = issued.expires_at
        activation_url = build_activation_url(
            settings.sheaf_base_url or "", channel.id, issued.code
        )
        activation_expires = issued.expires_at
    else:
        # Direct: usable immediately.
        channel.destination_state = DestinationState.ACTIVE.value

    db.add(channel)
    await db.flush()

    for r in body.group_rules:
        db.add(
            NotificationChannelGroupRule(
                channel_id=channel.id,
                group_id=r.group_id,
                rule=r.rule,
                include_private=r.include_private,
            )
        )
    for r in body.member_rules:
        db.add(
            NotificationChannelMemberRule(
                channel_id=channel.id,
                member_id=r.member_id,
                rule=r.rule,
            )
        )

    await db.commit()
    fresh = await _load_owned_channel(db, user, channel.id)
    return ChannelCreateResponse(
        channel=_channel_to_read(fresh),
        activation_url=activation_url,
        activation_expires_at=activation_expires,
    )


@router.get(
    "/watch-tokens/{token_id}/channels",
    response_model=list[ChannelRead],
)
async def list_channels(
    token_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChannelRead]:
    token = await _load_owned_token(db, user, token_id)
    result = await db.execute(
        select(NotificationChannel)
        .where(NotificationChannel.watch_token_id == token.id)
        .options(
            selectinload(NotificationChannel.group_rules),
            selectinload(NotificationChannel.member_rules),
        )
        .order_by(NotificationChannel.created_at.desc())
    )
    return [_channel_to_read(c) for c in result.scalars().all()]


@router.get("/channels/{channel_id}", response_model=ChannelRead)
async def get_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    channel = await _load_owned_channel(db, user, channel_id)
    return _channel_to_read(channel)


@router.patch(
    "/channels/{channel_id}",
    response_model=ChannelRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def update_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    channel = await _load_owned_channel(db, user, channel_id)

    if body.name is not None:
        channel.name = body.name
    if body.destination_config is not None:
        # Validate direct types still have required fields after merge.
        new_cfg = {**(channel.destination_config or {}), **body.destination_config}
        if channel.destination_type in _DIRECT_TYPES:
            _validate_direct_config(channel.destination_type, new_cfg)
        channel.destination_config = new_cfg
    if body.webhook_secret is not None:
        if channel.destination_type != DestinationType.WEBHOOK.value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="webhook_secret only valid for webhook channels",
            )
        channel.webhook_secret_encrypted = encrypt(body.webhook_secret)
    for field in (
        "base_all_members",
        "base_include_private",
        "trigger_on_start",
        "trigger_on_stop",
        "trigger_on_cofront_change",
        "cofront_redaction",
        "payload_sensitivity",
        "debounce_seconds",
        "aggregation_window_seconds",
    ):
        v = getattr(body, field)
        if v is not None:
            setattr(channel, field, v)
    if body.quiet_hours is not None:
        channel.quiet_hours = body.quiet_hours.model_dump()

    if body.group_rules is not None:
        channel.group_rules.clear()
        await db.flush()
        for r in body.group_rules:
            db.add(
                NotificationChannelGroupRule(
                    channel_id=channel.id,
                    group_id=r.group_id,
                    rule=r.rule,
                    include_private=r.include_private,
                )
            )
    if body.member_rules is not None:
        channel.member_rules.clear()
        await db.flush()
        for r in body.member_rules:
            db.add(
                NotificationChannelMemberRule(
                    channel_id=channel.id,
                    member_id=r.member_id,
                    rule=r.rule,
                )
            )

    await db.commit()
    return _channel_to_read(await _load_owned_channel(db, user, channel.id))


@router.delete(
    "/channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def delete_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    channel = await _load_owned_channel(db, user, channel_id)
    await db.delete(channel)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/channels/{channel_id}/enable",
    response_model=ChannelRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def enable_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    """Re-activate a disabled channel.

    Re-enable on a `pending_registration` channel is a no-op (the activation
    flow is what flips it to active). Disabled channels (whether unsubscribed
    by the recipient, paused by the owner, or auto-disabled after a permanent
    delivery failure) flip back to active.
    """
    channel = await _load_owned_channel(db, user, channel_id)
    if channel.destination_state == DestinationState.PENDING_REGISTRATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Channel is awaiting activation; can't enable until redeemed",
        )
    channel.destination_state = DestinationState.ACTIVE.value
    await db.commit()
    return _channel_to_read(await _load_owned_channel(db, user, channel.id))


@router.post(
    "/channels/{channel_id}/disable",
    response_model=ChannelRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def disable_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    """Pause a channel without deleting it. Stops dispatch immediately;
    re-enable later to resume."""
    channel = await _load_owned_channel(db, user, channel_id)
    channel.destination_state = DestinationState.DISABLED.value
    await db.commit()
    return _channel_to_read(await _load_owned_channel(db, user, channel.id))


# ---------- duplicate / re-issue / test / preview --------------------------


@router.post(
    "/channels/{channel_id}/duplicate",
    response_model=ChannelCreateResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def duplicate_channel(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelCreateResponse:
    src = await _load_owned_channel(db, user, channel_id)

    clone = NotificationChannel(
        id=uuid.uuid4(),
        watch_token_id=src.watch_token_id,
        name=f"{src.name} (copy)",
        destination_type=src.destination_type,
        # Webhook clones must not carry the URL or secret; owner re-enters.
        destination_config={}
        if src.destination_type == DestinationType.WEBHOOK.value
        else dict(src.destination_config or {}),
        base_all_members=src.base_all_members,
        base_include_private=src.base_include_private,
        trigger_on_start=src.trigger_on_start,
        trigger_on_stop=src.trigger_on_stop,
        trigger_on_cofront_change=src.trigger_on_cofront_change,
        cofront_redaction=src.cofront_redaction,
        payload_sensitivity=src.payload_sensitivity,
        debounce_seconds=src.debounce_seconds,
        aggregation_window_seconds=src.aggregation_window_seconds,
        quiet_hours=src.quiet_hours,
    )

    activation_url = None
    activation_expires = None
    if src.destination_type in _PUSH_TYPES:
        issued = issue_activation_code(ttl_days=settings.activation_code_ttl_days)
        clone.destination_state = DestinationState.PENDING_REGISTRATION.value
        clone.activation_code_hash = issued.code_hash
        clone.activation_code_expires_at = issued.expires_at
        activation_url = build_activation_url(
            settings.sheaf_base_url or "", clone.id, issued.code
        )
        activation_expires = issued.expires_at
    elif src.destination_type == DestinationType.WEBHOOK.value:
        # Webhook clones land in pending_registration too; owner must re-enter
        # URL + secret before the channel becomes deliverable.
        clone.destination_state = DestinationState.PENDING_REGISTRATION.value
    else:
        clone.destination_state = DestinationState.ACTIVE.value

    db.add(clone)
    await db.flush()
    for r in src.group_rules:
        db.add(
            NotificationChannelGroupRule(
                channel_id=clone.id,
                group_id=r.group_id,
                rule=r.rule,
                include_private=r.include_private,
            )
        )
    for r in src.member_rules:
        db.add(
            NotificationChannelMemberRule(
                channel_id=clone.id,
                member_id=r.member_id,
                rule=r.rule,
            )
        )
    await db.commit()
    fresh = await _load_owned_channel(db, user, clone.id)
    return ChannelCreateResponse(
        channel=_channel_to_read(fresh),
        activation_url=activation_url,
        activation_expires_at=activation_expires,
    )


@router.post(
    "/channels/{channel_id}/reissue-activation",
    response_model=ReissueActivationResponse,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def reissue_activation(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReissueActivationResponse:
    channel = await _load_owned_channel(db, user, channel_id)
    if channel.destination_state != DestinationState.PENDING_REGISTRATION.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel is not in pending_registration state",
        )
    if channel.destination_type not in _PUSH_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="re-issue only applies to push-style destinations",
        )

    issued = issue_activation_code(ttl_days=settings.activation_code_ttl_days)
    channel.activation_code_hash = issued.code_hash
    channel.activation_code_expires_at = issued.expires_at
    await db.commit()
    return ReissueActivationResponse(
        activation_url=build_activation_url(
            settings.sheaf_base_url or "", channel.id, issued.code
        ),
        activation_expires_at=issued.expires_at,
    )


@router.post(
    "/channels/{channel_id}/test",
    response_model=TestDispatchResponse,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def send_test(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TestDispatchResponse:
    channel = await _load_owned_channel(db, user, channel_id)
    if channel.destination_state != DestinationState.ACTIVE.value:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="channel is not active",
        )

    from sheaf.services.notifications.payload import RenderedMessage

    message = RenderedMessage(
        title="Sheaf test notification",
        body=f"This is a test from your channel '{channel.name}'.",
    )
    outcome = await deliver(channel, message, event_id=str(uuid.uuid4()))
    if outcome.ok:
        channel.last_delivered_at = datetime.now(UTC)
        await db.commit()
    return TestDispatchResponse(delivered=outcome.ok, error=outcome.error)


@router.post(
    "/channels/{channel_id}/preview",
    response_model=PreviewResponse,
)
async def preview_channel(
    channel_id: uuid.UUID,
    body: ChannelUpdate | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PreviewResponse:
    """Resolve every member of the system against the channel's filter and
    return the included/excluded split with attribution.

    If `body` is provided, it overrides the channel's stored config in-memory
    only, used by the frontend's live preview while the owner edits.
    """
    channel = await _load_owned_channel(db, user, channel_id)

    # Apply transient overrides (non-persistent).
    if body is not None:
        if body.base_all_members is not None:
            channel.base_all_members = body.base_all_members
        if body.base_include_private is not None:
            channel.base_include_private = body.base_include_private
        if body.group_rules is not None:
            channel.group_rules = [
                NotificationChannelGroupRule(
                    channel_id=channel.id,
                    group_id=r.group_id,
                    rule=r.rule,
                    include_private=r.include_private,
                )
                for r in body.group_rules
            ]
        if body.member_rules is not None:
            channel.member_rules = [
                NotificationChannelMemberRule(
                    channel_id=channel.id,
                    member_id=r.member_id,
                    rule=r.rule,
                )
                for r in body.member_rules
            ]

    # Pull every member of this system with their groups.
    system = await _system_for_user(user, db)
    member_rows = await db.execute(
        select(Member)
        .where(Member.system_id == system.id)
        .options(selectinload(Member.groups))
    )
    members = list(member_rows.scalars().all())

    # Group name lookup for attribution prettification.
    group_rows = await db.execute(
        select(Group).where(Group.system_id == system.id)
    )
    name_lookup = build_group_name_lookup(group_rows.scalars().all())

    included: list[PreviewMember] = []
    excluded: list[PreviewMember] = []
    warnings: list[str] = []
    private_via_override: dict[uuid.UUID, list[str]] = {}

    for m in members:
        result = resolve_member_visibility(
            channel, m, [g.id for g in m.groups], group_name_lookup=name_lookup
        )
        is_private = m.privacy != PrivacyLevel.PUBLIC
        # Member.name is encrypted at rest; display_name is plaintext.
        display = m.display_name or member_name_plaintext(m)
        entry = PreviewMember(
            member_id=m.id,
            name=display,
            is_private=is_private,
            attribution=result.attribution,
        )
        if result.included:
            included.append(entry)
            if is_private and "privacy override" in result.attribution:
                # Track which group rule pulled this private member in.
                private_via_override.setdefault(m.id, []).append(result.attribution)
        else:
            excluded.append(entry)

    # Surface privacy-override warnings (one per group rule).
    if private_via_override:
        names = sorted({m.name for m in included if m.member_id in private_via_override})
        warnings.append(
            f"Privacy override: {len(names)} private member(s) included via L2 rule "
            f"({', '.join(names)})."
        )

    return PreviewResponse(included=included, excluded=excluded, warnings=warnings)


# ---------- group / member rule sub-endpoints ------------------------------


@router.post(
    "/channels/{channel_id}/group-rules",
    response_model=ChannelRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def add_group_rule(
    channel_id: uuid.UUID,
    body: GroupRuleSpec,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    channel = await _load_owned_channel(db, user, channel_id)
    # Replace any existing rule for this group.
    channel.group_rules[:] = [r for r in channel.group_rules if r.group_id != body.group_id]
    await db.flush()
    db.add(
        NotificationChannelGroupRule(
            channel_id=channel.id,
            group_id=body.group_id,
            rule=body.rule,
            include_private=body.include_private,
        )
    )
    await db.commit()
    return _channel_to_read(await _load_owned_channel(db, user, channel.id))


@router.delete(
    "/channels/{channel_id}/group-rules/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def remove_group_rule(
    channel_id: uuid.UUID,
    group_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    channel = await _load_owned_channel(db, user, channel_id)
    channel.group_rules[:] = [r for r in channel.group_rules if r.group_id != group_id]
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/channels/{channel_id}/member-rules",
    response_model=ChannelRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def add_member_rule(
    channel_id: uuid.UUID,
    body: MemberRuleSpec,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChannelRead:
    channel = await _load_owned_channel(db, user, channel_id)
    channel.member_rules[:] = [r for r in channel.member_rules if r.member_id != body.member_id]
    await db.flush()
    db.add(
        NotificationChannelMemberRule(
            channel_id=channel.id,
            member_id=body.member_id,
            rule=body.rule,
        )
    )
    await db.commit()
    return _channel_to_read(await _load_owned_channel(db, user, channel.id))


@router.delete(
    "/channels/{channel_id}/member-rules/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def remove_member_rule(
    channel_id: uuid.UUID,
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    channel = await _load_owned_channel(db, user, channel_id)
    channel.member_rules[:] = [r for r in channel.member_rules if r.member_id != member_id]
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------- "receiving" dashboard ------------------------------------------
#
# When a recipient redeems an activation code while signed in to a Sheaf
# account, the channel binds to their user_id. These endpoints let that user
# see and manage every channel they're receiving (across all systems they
# subscribe to) without juggling per-channel capability URLs.


@router.get("/notifications/receiving", response_model=list[ReceivingChannelView])
async def list_receiving(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ReceivingChannelView]:
    """List channels currently delivering to this account."""
    result = await db.execute(
        select(NotificationChannel, WatchToken, System)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .join(System, WatchToken.system_id == System.id)
        .where(NotificationChannel.redeemed_by_account_id == user.id)
        .order_by(NotificationChannel.redeemed_at.desc())
    )
    rows: list[ReceivingChannelView] = []
    for channel, _token, system in result.all():
        rows.append(
            ReceivingChannelView(
                channel_id=channel.id,
                channel_name=channel.name,
                system_label=getattr(system, "display_name", None),
                destination_type=channel.destination_type,
                destination_state=channel.destination_state,
                redeemed_at=channel.redeemed_at,
                last_delivered_at=channel.last_delivered_at,
            )
        )
    return rows


@router.post(
    "/notifications/receiving/{channel_id}/unsubscribe",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unsubscribe_receiving(
    channel_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Disable a channel that delivers to this account.

    Same effect as the capability-URL unsubscribe (`destination_state =
    'disabled'`), but the auth check is the user's session matching
    `redeemed_by_account_id`. The owner sees the channel disabled but isn't
    told who unsubscribed or when.
    """
    result = await db.execute(
        select(NotificationChannel).where(
            NotificationChannel.id == channel_id,
            NotificationChannel.redeemed_by_account_id == user.id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Channel not found"
        )
    channel.destination_state = DestinationState.DISABLED.value
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
