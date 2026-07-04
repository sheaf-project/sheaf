"""Reminders API.

CRUD endpoints for the two reminder kinds (automated front-event
triggered, and repeated cron/structured-schedule). Both kinds are
gated by the `notifications:write` scope on the assumption that a
caller permitted to manage notification destinations is also
permitted to manage reminders that ride those destinations.
"""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.middleware.rate_limit import write_rate_limit
from sheaf.models.member import Member
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.pending_action import PendingActionType
from sheaf.models.reminder import Reminder
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.observability.metrics import reminders_created_total
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.schemas.reminder import (
    ReminderCreate,
    ReminderRead,
    ReminderUpdate,
)
from sheaf.services.reminders import (
    compute_next_fire,
    decrypt_for_read,
    encrypt_title_body,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(prefix="/reminders", tags=["reminders"])


# --- Helpers ---------------------------------------------------------------


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="System not found"
        )
    return system


async def _get_owned_reminder(
    reminder_id: uuid.UUID, system: System, db: AsyncSession
) -> Reminder:
    result = await db.execute(
        select(Reminder)
        .options(
            selectinload(Reminder.scope_members),
            selectinload(Reminder.pending),
        )
        .where(
            Reminder.id == reminder_id,
            Reminder.system_id == system.id,
        )
    )
    reminder = result.scalar_one_or_none()
    if reminder is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reminder not found"
        )
    return reminder


async def _validate_channel(
    channel_id: uuid.UUID, system: System, db: AsyncSession
) -> NotificationChannel:
    """Confirm the supplied channel belongs to the user's system.

    Reminders point to a channel via `channel_id`; the channel itself
    belongs to a watch token, which belongs to a system. We refuse
    reminders that would send through a different system's channel.
    """
    result = await db.execute(
        select(NotificationChannel)
        .join(WatchToken, NotificationChannel.watch_token_id == WatchToken.id)
        .where(
            NotificationChannel.id == channel_id,
            WatchToken.system_id == system.id,
        )
    )
    channel = result.scalar_one_or_none()
    if channel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification channel not found.",
        )
    return channel


async def _validate_scope_members(
    member_ids: list[uuid.UUID], system: System, db: AsyncSession
) -> list[Member]:
    """Confirm all member ids exist within this system."""
    if not member_ids:
        return []
    result = await db.execute(
        select(Member).where(
            Member.id.in_(member_ids),
            Member.system_id == system.id,
        )
    )
    members = list(result.scalars().all())
    if len(members) != len(member_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more scope_member_ids are invalid.",
        )
    return members


def _validate_trigger_config(
    trigger_type: str,
    *,
    trigger_event: str | None,
    delay_seconds: int | None,
    schedule_kind: str | None,
    schedule_time: str | None,
    schedule_dow_mask: int | None,
    schedule_dom: int | None,
    schedule_tz: str | None,
    cron_expression: str | None,
) -> None:
    """Cross-field validation for the trigger config.

    Pydantic catches per-field shapes; this layer enforces the bigger
    "what fields are required given the trigger type" rules.
    """
    if trigger_type == "automated":
        if delay_seconds is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="automated reminders require delay_seconds.",
            )
        if trigger_event not in ("start", "stop", "any"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="trigger_event must be start | stop | any.",
            )
        return

    if trigger_type != "repeated":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="trigger_type must be automated or repeated.",
        )

    # Repeated: either cron_expression OR a structured schedule.
    if cron_expression:
        try:
            croniter(cron_expression)
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid cron expression: {exc}",
            ) from exc
    else:
        if schedule_kind not in ("daily", "weekly", "monthly"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "repeated reminders require either cron_expression or a "
                    "structured schedule_kind (daily | weekly | monthly)."
                ),
            )
        if schedule_time is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="schedule_time (HH:MM) is required for structured schedules.",
            )
        if schedule_kind == "weekly" and not schedule_dow_mask:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="weekly schedules require schedule_dow_mask (1-127).",
            )
        if schedule_kind == "monthly" and not schedule_dom:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="monthly schedules require schedule_dom (1-31).",
            )

    if schedule_tz is not None:
        try:
            ZoneInfo(schedule_tz)
        except ZoneInfoNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown timezone: {schedule_tz}",
            ) from exc


def _to_read(
    reminder: Reminder, *, pending_delete_at: datetime | None = None
) -> ReminderRead:
    decrypted = decrypt_for_read(reminder)
    return ReminderRead(
        id=reminder.id,
        system_id=reminder.system_id,
        channel_id=reminder.channel_id,
        name=reminder.name,
        title=decrypted["title"],
        body=decrypted["body"],
        enabled=reminder.enabled,
        trigger_type=reminder.trigger_type,
        trigger_member_id=reminder.trigger_member_id,
        trigger_event=reminder.trigger_event,
        delay_seconds=reminder.delay_seconds,
        schedule_kind=reminder.schedule_kind,
        schedule_time=reminder.schedule_time,
        schedule_dow_mask=reminder.schedule_dow_mask,
        schedule_dom=reminder.schedule_dom,
        schedule_tz=reminder.schedule_tz,
        cron_expression=reminder.cron_expression,
        scope=reminder.scope,
        scope_member_ids=decrypted["scope_member_ids"],
        digest_when_absent=reminder.digest_when_absent,
        last_fired_at=reminder.last_fired_at,
        pending_count=decrypted["pending_count"],
        next_fire_at=decrypted["next_fire_at"],
        created_at=reminder.created_at,
        updated_at=reminder.updated_at,
        pending_delete_at=pending_delete_at,
    )


# --- CRUD -----------------------------------------------------------------


@router.get("", response_model=list[ReminderRead])
async def list_reminders(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(Reminder)
        .options(
            selectinload(Reminder.scope_members),
            selectinload(Reminder.pending),
        )
        .where(Reminder.system_id == system.id)
        .order_by(Reminder.created_at.desc())
    )
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.REMINDER_DELETE
    )
    return [
        _to_read(r, pending_delete_at=pending.get(r.id))
        for r in result.scalars().all()
    ]


@router.post(
    "",
    response_model=ReminderRead,
    status_code=status.HTTP_201_CREATED,
    # write_rate_limit(): shared per-account write budget (see fronts).
    dependencies=[Depends(require_scope("notifications:write")), write_rate_limit()],
)
async def create_reminder(
    body: ReminderCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    await _validate_channel(body.channel_id, system, db)
    _validate_trigger_config(
        body.trigger_type,
        trigger_event=body.trigger_event,
        delay_seconds=body.delay_seconds,
        schedule_kind=body.schedule_kind,
        schedule_time=body.schedule_time,
        schedule_dow_mask=body.schedule_dow_mask,
        schedule_dom=body.schedule_dom,
        schedule_tz=body.schedule_tz,
        cron_expression=body.cron_expression,
    )
    scope_members = await _validate_scope_members(
        body.scope_member_ids, system, db
    )

    title_ct, body_ct = encrypt_title_body(body.title, body.body)
    reminder = Reminder(
        id=uuid.uuid4(),
        system_id=system.id,
        channel_id=body.channel_id,
        name=body.name,
        title=title_ct,
        body=body_ct,
        enabled=body.enabled,
        trigger_type=body.trigger_type,
        trigger_member_id=body.trigger_member_id,
        trigger_event=body.trigger_event,
        delay_seconds=body.delay_seconds,
        schedule_kind=body.schedule_kind,
        schedule_time=body.schedule_time,
        schedule_dow_mask=body.schedule_dow_mask,
        schedule_dom=body.schedule_dom,
        schedule_tz=body.schedule_tz,
        cron_expression=body.cron_expression,
        scope=body.scope,
        digest_when_absent=body.digest_when_absent,
        scope_members=scope_members,
    )
    db.add(reminder)
    await db.commit()
    reminders_created_total.inc()
    # Re-fetch with relations for the response shape.
    refreshed = await _get_owned_reminder(reminder.id, system, db)
    return _to_read(refreshed)


@router.get("/{reminder_id}", response_model=ReminderRead)
async def get_reminder(
    reminder_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    reminder = await _get_owned_reminder(reminder_id, system, db)
    pending = await pending_finalize_after_by_target(
        db, system, PendingActionType.REMINDER_DELETE
    )
    return _to_read(reminder, pending_delete_at=pending.get(reminder.id))


@router.patch(
    "/{reminder_id}",
    response_model=ReminderRead,
    dependencies=[Depends(require_scope("notifications:write"))],
)
async def update_reminder(
    reminder_id: uuid.UUID,
    body: ReminderUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    reminder = await _get_owned_reminder(reminder_id, system, db)

    update_data = body.model_dump(exclude_unset=True)

    if "channel_id" in update_data:
        await _validate_channel(update_data["channel_id"], system, db)

    # Re-run cross-field validation against the post-update view of the
    # reminder so a partial PATCH that flips trigger_type doesn't produce
    # a half-valid config.
    next_view = {**_reminder_dict(reminder), **update_data}
    _validate_trigger_config(
        next_view["trigger_type"],
        trigger_event=next_view.get("trigger_event"),
        delay_seconds=next_view.get("delay_seconds"),
        schedule_kind=next_view.get("schedule_kind"),
        schedule_time=next_view.get("schedule_time"),
        schedule_dow_mask=next_view.get("schedule_dow_mask"),
        schedule_dom=next_view.get("schedule_dom"),
        schedule_tz=next_view.get("schedule_tz"),
        cron_expression=next_view.get("cron_expression"),
    )

    if "scope_member_ids" in update_data:
        members = await _validate_scope_members(
            update_data["scope_member_ids"], system, db
        )
        reminder.scope_members = members
        del update_data["scope_member_ids"]

    # Encrypt title/body when they change. Other fields just `setattr`.
    if "title" in update_data or "body" in update_data:
        new_title = update_data.get("title", _decrypted(reminder.title) or "")
        new_body = update_data.get(
            "body", _decrypted(reminder.body)
        )
        title_ct, body_ct = encrypt_title_body(new_title, new_body)
        reminder.title = title_ct
        reminder.body = body_ct
        update_data.pop("title", None)
        update_data.pop("body", None)

    for key, value in update_data.items():
        setattr(reminder, key, value)

    await db.commit()
    refreshed = await _get_owned_reminder(reminder.id, system, db)
    return _to_read(refreshed)


@router.delete(
    "/{reminder_id}",
    dependencies=[Depends(require_scope("notifications:delete"))],
)
async def delete_reminder(
    reminder_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Delete a reminder.

    Reuses the System Safety machinery for delete protection: when the
    notifications safety category is enabled with a non-zero grace
    period, the delete is queued as a pending action and executed by
    the background finalizer. Step-up auth (password / TOTP) gates the
    request via `verify_destructive_auth`, matching the behaviour of
    notification channel deletion.
    """
    system = await _get_user_system(user, db)
    reminder = await _get_owned_reminder(reminder_id, system, db)
    await verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
        db,
    )

    if is_safeguarded(system, PendingActionType.REMINDER_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.REMINDER_DELETE,
            target_id=reminder.id,
            target_label=reminder.name,
        )
        await db.commit()
        await db.refresh(pending)
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "pending_action_id": str(pending.id),
                "finalize_after": pending.finalize_after.isoformat(),
            },
        )

    await db.delete(reminder)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{reminder_id}/next-fire", response_model=dict)
async def get_next_fire(
    reminder_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the next scheduled fire time, useful for UI previews
    of cron expressions and structured schedules."""
    system = await _get_user_system(user, db)
    reminder = await _get_owned_reminder(reminder_id, system, db)
    next_fire = compute_next_fire(reminder)
    return {"next_fire_at": next_fire.isoformat() if next_fire else None}


# --- Internal helpers -----------------------------------------------------


def _decrypted(ciphertext: str | None) -> str | None:
    from sheaf.crypto import decrypt

    return decrypt(ciphertext) if ciphertext else None


def _reminder_dict(reminder: Reminder) -> dict:
    """Snapshot the validation-relevant fields of an existing reminder
    for cross-field PATCH validation."""
    return {
        "trigger_type": reminder.trigger_type,
        "trigger_event": reminder.trigger_event,
        "delay_seconds": reminder.delay_seconds,
        "schedule_kind": reminder.schedule_kind,
        "schedule_time": reminder.schedule_time,
        "schedule_dow_mask": reminder.schedule_dow_mask,
        "schedule_dom": reminder.schedule_dom,
        "schedule_tz": reminder.schedule_tz,
        "cron_expression": reminder.cron_expression,
    }
