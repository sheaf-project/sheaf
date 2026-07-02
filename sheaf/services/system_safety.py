"""System Safety service layer.

Contains:
- Auth helper shared by every destructive action endpoint.
- Loosening detection for safety-setting changes (asymmetric delay).
- Fronting snapshot helper used when queuing pending actions.
- Dispatcher for finalizing pending actions against the right model.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.crypto import decrypt
from sheaf.models.content_revision import ContentRevision
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.message import Message
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.pending_action import (
    PendingAction,
    PendingActionStatus,
    PendingActionType,
)
from sheaf.models.poll import Poll
from sheaf.models.reminder import Reminder
from sheaf.models.safety_change_request import (
    SafetyChangeRequest,
    SafetyChangeStatus,
)
from sheaf.models.system import DeleteConfirmation, System
from sheaf.models.tag import Tag
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken

# Categories that safety can apply to — kept in one place so the API,
# schemas, and finalize dispatcher all agree on the set.
SAFETY_CATEGORIES: tuple[str, ...] = (
    "members",
    "groups",
    "tags",
    "fields",
    "fronts",
    "journals",
    "images",
    "revisions",
    "notifications",
    "reminders",
    "polls",
    "messages",
    # Unlike the others, "archive" has no grace-able PendingAction; it only
    # gates whether archiving a member requires re-auth (checked directly in
    # the archive endpoint). Listed here so the settings surface treats it
    # like any other toggle (and loosening it routes through the asymmetric
    # delay via split_safety_changes).
    "archive",
)

_CATEGORY_BY_ACTION: dict[str, str] = {
    PendingActionType.MEMBER_DELETE: "members",
    PendingActionType.GROUP_DELETE: "groups",
    PendingActionType.TAG_DELETE: "tags",
    PendingActionType.FIELD_DELETE: "fields",
    PendingActionType.FRONT_DELETE: "fronts",
    PendingActionType.JOURNAL_DELETE: "journals",
    PendingActionType.IMAGE_DELETE: "images",
    PendingActionType.REVISION_UNPIN: "revisions",
    PendingActionType.WATCH_TOKEN_REVOKE: "notifications",
    PendingActionType.CHANNEL_DELETE: "notifications",
    PendingActionType.REMINDER_DELETE: "reminders",
    PendingActionType.POLL_DELETE: "polls",
    # Both single-message and thread delete map to the same category for
    # v1; the per-operation auth-tier split is parked under the
    # System Safety v2 future-work entry.
    PendingActionType.MESSAGE_DELETE: "messages",
    PendingActionType.MESSAGE_THREAD_DELETE: "messages",
}

_MODEL_BY_ACTION: dict[str, type] = {
    PendingActionType.MEMBER_DELETE: Member,
    PendingActionType.GROUP_DELETE: Group,
    PendingActionType.TAG_DELETE: Tag,
    PendingActionType.FIELD_DELETE: CustomFieldDefinition,
    PendingActionType.FRONT_DELETE: Front,
    PendingActionType.JOURNAL_DELETE: JournalEntry,
    PendingActionType.IMAGE_DELETE: UploadedFile,
    PendingActionType.REVISION_UNPIN: ContentRevision,
    PendingActionType.WATCH_TOKEN_REVOKE: WatchToken,
    PendingActionType.CHANNEL_DELETE: NotificationChannel,
    PendingActionType.REMINDER_DELETE: Reminder,
    PendingActionType.POLL_DELETE: Poll,
    # Both message-delete actions resolve to a single Message row at
    # finalize time. Thread-delete cascades to children inside
    # `finalize_pending_action`.
    PendingActionType.MESSAGE_DELETE: Message,
    PendingActionType.MESSAGE_THREAD_DELETE: Message,
}


# Auth tier strength — PASSWORD and TOTP are treated as equivalent
# strength (either-or, not both). NONE < PASSWORD/TOTP < BOTH.
_TIER_STRENGTH: dict[str, int] = {
    DeleteConfirmation.NONE: 0,
    DeleteConfirmation.PASSWORD: 1,
    DeleteConfirmation.TOTP: 1,
    DeleteConfirmation.BOTH: 2,
}


# ---------------------------------------------------------------------------
# Auth gate
# ---------------------------------------------------------------------------


async def verify_destructive_auth(
    user: User,
    system: System,
    password: str | None,
    totp_code: str | None,
    db: AsyncSession,
) -> None:
    """Raise HTTPException if the required re-auth for destructive actions isn't satisfied.

    Mirrors the existing member-delete pattern. Called at the top of every
    safeguarded destructive endpoint and again before finalizing safety-setting
    loosening changes.

    Failed attempts feed the unified lockout (`sheaf.auth.lockout`) so a
    hijacked session can't brute the password or the 6-digit TOTP space
    here without tripping the same counter that login does.
    """
    level = system.delete_confirmation
    needs_password = level in (DeleteConfirmation.PASSWORD, DeleteConfirmation.BOTH)
    needs_totp = level in (DeleteConfirmation.TOTP, DeleteConfirmation.BOTH)

    # Misconfiguration fail-safe: the tier requires TOTP but the user has no
    # TOTP enrolled. Both settings endpoints and the TOTP-disable path now
    # prevent reaching this state, so it only happens with legacy data —
    # fall back to a password check rather than silently waving the action
    # through (which, for a TOTP-only tier, would be no gate at all).
    if needs_totp and not user.totp_enabled:
        needs_password = True
        needs_totp = False

    # Only consult the lockout when a brute-forceable credential is about
    # to be verified — tier NONE checks nothing, so nothing to protect.
    if needs_password or needs_totp:
        ensure_not_locked(user)

    if needs_password:
        if not password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password required",
            )
        if not await verify_password(password, user.password_hash):
            await record_login_failure(db, user)
            # 403 (not 401) — the caller IS authenticated; the step-up
            # password gate denied this specific destructive action. 401
            # would trip the frontend's silent-refresh-and-retry path,
            # which is meaningless here (refreshing the access token
            # doesn't make the wrong password right) and hides the real
            # error from the user.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Incorrect password",
            )

    if needs_totp:
        if not totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, totp_code)
        if totp_result is not TotpCheck.OK:
            await record_login_failure(db, user, reason="totp_failures")
            # Same reasoning as the wrong-password branch: 403, not 401.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
            )


# ---------------------------------------------------------------------------
# Safety settings change detection (asymmetric loosening delay)
# ---------------------------------------------------------------------------


@dataclass
class SafetyChangeSplit:
    applied: dict[str, Any]
    deferred: dict[str, Any]


_RETENTION_FIELDS = (
    "journal_max_revisions",
    "journal_max_revision_days",
    "pinned_revision_max_per_target",
)


def split_safety_changes(system: System, updates: dict[str, Any]) -> SafetyChangeSplit:
    """Split proposed updates into immediate (tighten) vs. deferred (loosen).

    Loosening means:
      - grace period reduced
      - delete_confirmation tier strength reduced
      - any safety_applies_to_<category> flipped True -> False
      - revision-retention cap reduced (None -> N or N -> M with M<N)
    """
    applied: dict[str, Any] = {}
    deferred: dict[str, Any] = {}

    for field, new_value in updates.items():
        # For retention fields, None is a valid value meaning "clear override".
        # For everything else, None means "field not provided in update".
        if new_value is None and field not in _RETENTION_FIELDS:
            continue
        current = getattr(system, field, None)
        if current == new_value:
            continue

        if field == "safety_grace_period_days":
            if new_value < current:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        elif field == "delete_confirmation":
            new_strength = _TIER_STRENGTH.get(new_value, 0)
            old_strength = _TIER_STRENGTH.get(current, 0)
            if new_strength < old_strength:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        elif field.startswith("safety_applies_to_") or field == "auto_pin_first_revision":
            if current is True and new_value is False:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        elif field in _RETENTION_FIELDS:
            # For a revision-retention cap, BOTH None ("use tier default") and
            # 0 ("unlimited") mean the loosest possible cap - keep everything -
            # so both map to +infinity. The deferred/guarded path is the
            # data-destroying direction: the effective cap getting *smaller*
            # (unlimited -> a finite N, or N -> M with M<N), because a smaller
            # cap widens what the GC sweep deletes. Raising the cap, or moving
            # to unlimited, keeps more and applies immediately.
            #
            # Treating a stored 0 as the literal integer zero (the prior bug)
            # made 0 sort as the *smallest* cap, so a 0 -> 5 change - the most
            # destructive transition, since it turns "keep everything" into
            # "delete all but the newest 5" - passed `5 < 0` == False and was
            # applied immediately with no grace and no re-auth. Mapping 0 to
            # +infinity puts it back on the deferred path.
            new_eff = float("inf") if new_value is None or new_value == 0 else new_value
            cur_eff = float("inf") if current is None or current == 0 else current
            if new_eff < cur_eff:
                deferred[field] = new_value
            else:
                applied[field] = new_value
        else:
            # Unknown field — apply immediately (router should reject unknowns upstream).
            applied[field] = new_value

    return SafetyChangeSplit(applied=applied, deferred=deferred)


# ---------------------------------------------------------------------------
# Fronting snapshot
# ---------------------------------------------------------------------------


async def snapshot_current_fronts(
    system_id: uuid.UUID, db: AsyncSession
) -> tuple[list[str], list[str]]:
    """Return (member_ids, member_names) for members currently fronting."""
    result = await db.execute(
        select(Member)
        .join(Member.fronts)
        .where(Front.system_id == system_id, Front.ended_at.is_(None))
        .distinct()
    )
    members = result.scalars().all()
    # Member.name is encrypted; decrypt for the display-snapshot. display_name
    # is plaintext and used as-is when set.
    from sheaf.crypto import decrypt

    return (
        [str(m.id) for m in members],
        [m.display_name or decrypt(m.name) for m in members],
    )


# ---------------------------------------------------------------------------
# Queue pending action
# ---------------------------------------------------------------------------


def is_safeguarded(system: System, action_type: str) -> bool:
    """True if grace > 0 AND the category toggle is on for this action type."""
    if system.safety_grace_period_days <= 0:
        return False
    category = _CATEGORY_BY_ACTION.get(action_type)
    if category is None:
        return False
    return bool(getattr(system, f"safety_applies_to_{category}"))


async def queue_pending_action(
    *,
    db: AsyncSession,
    system: System,
    user: User,
    action_type: str,
    target_id: uuid.UUID,
    target_label: str,
) -> PendingAction:
    """Create a PendingAction row with a fronting snapshot. Caller commits."""
    fronting_ids, fronting_names = await snapshot_current_fronts(system.id, db)
    now = datetime.now(UTC)
    pending = PendingAction(
        system_id=system.id,
        action_type=action_type,
        target_id=target_id,
        target_label=target_label,
        requested_at=now,
        requested_by_user_id=user.id,
        finalize_after=now + timedelta(days=system.safety_grace_period_days),
        fronting_member_ids=fronting_ids,
        fronting_member_names=fronting_names,
        status=PendingActionStatus.PENDING,
    )
    db.add(pending)
    return pending


def has_pending_action_for_target(
    db_actions: list[PendingAction], target_id: uuid.UUID, action_type: str
) -> bool:
    """Check a preloaded list for an existing pending action on this target."""
    return any(
        p.target_id == target_id
        and p.action_type == action_type
        and p.status == PendingActionStatus.PENDING
        for p in db_actions
    )


async def pending_finalize_after_by_target(
    db: AsyncSession,
    system_or_id: System | uuid.UUID,
    action_type: PendingActionType,
) -> dict[uuid.UUID, datetime]:
    """Map target_id -> finalize_after for every still-pending action of one
    type in this system.

    List endpoints call this once per request to flag pending-delete items
    in their Read responses (a `pending_delete_at` field driving the badge
    in the UI). Cancelled / completed / errored actions are excluded so a
    re-queue after cancel shows up correctly.

    Optimisation: when callers pass the `System` object directly and its
    safety grace period is 0, no new pending actions can be queued, so
    we skip the query entirely. Old rows in the table (from when grace
    was non-zero) get swept by the periodic finalize job and are
    typically gone within minutes; the list endpoints temporarily not
    flagging those stragglers is acceptable. Callers that only have an
    id pay for the full query as before.
    """
    if isinstance(system_or_id, System):
        if system_or_id.safety_grace_period_days <= 0:
            return {}
        system_id = system_or_id.id
    else:
        system_id = system_or_id

    result = await db.execute(
        select(PendingAction.target_id, PendingAction.finalize_after)
        .where(
            PendingAction.system_id == system_id,
            PendingAction.action_type == action_type.value,
            PendingAction.status == PendingActionStatus.PENDING.value,
        )
    )
    return {row.target_id: row.finalize_after for row in result}


# ---------------------------------------------------------------------------
# Finalize pending action
# ---------------------------------------------------------------------------


async def finalize_pending_action(
    pending: PendingAction, db: AsyncSession
) -> None:
    """Execute the queued action. Idempotent: missing target marks completed.

    Most action types are deletes, with cascade/blob cleanup as needed.
    REVISION_UNPIN clears the pin flag in place rather than deleting.
    """
    model = _MODEL_BY_ACTION.get(pending.action_type)
    if model is None:
        pending.status = PendingActionStatus.ERRORED
        pending.error_message = f"Unknown action_type: {pending.action_type}"
        pending.completed_at = datetime.now(UTC)
        return

    target = await db.get(model, pending.target_id)
    if target is not None and await _target_in_scope(target, pending, db):
        if pending.action_type == PendingActionType.REVISION_UNPIN:
            from sheaf.services.journals import unpin_revision_immediate

            unpin_revision_immediate(target)
        elif pending.action_type == PendingActionType.WATCH_TOKEN_REVOKE:
            # Soft-revoke: matches the immediate revoke path, channels stay in
            # DB but the dispatcher skips them. Idempotent if already revoked.
            if target.revoked_at is None:
                target.revoked_at = datetime.now(UTC)
        else:
            # Polymorphic content_revisions can't FK on target — cascade in app.
            if pending.action_type == PendingActionType.JOURNAL_DELETE:
                from sheaf.services.journals import delete_revisions_for

                await delete_revisions_for("journal_entry", target.id, db)
            elif pending.action_type == PendingActionType.MEMBER_DELETE:
                from sheaf.services.journals import delete_revisions_for

                await delete_revisions_for("member_bio", target.id, db)
            elif pending.action_type == PendingActionType.MESSAGE_DELETE:
                from sheaf.services.journals import delete_revisions_for

                await delete_revisions_for("message", target.id, db)
            elif pending.action_type == PendingActionType.MESSAGE_THREAD_DELETE:
                # Thread delete: cascade to all replies in the chain.
                # Walk the parent_message_id graph and delete each
                # message + its revisions. parent FK is SET NULL on
                # delete, so order doesn't matter for FK consistency,
                # but we sweep top-down for predictability.
                from sheaf.services.journals import delete_revisions_for
                from sheaf.services.messages import collect_thread_ids

                thread_ids = await collect_thread_ids(db, target.id)
                for mid in thread_ids:
                    await delete_revisions_for("message", mid, db)
                    msg = await db.get(Message, mid)
                    if msg is not None:
                        await db.delete(msg)
                # The root target is part of thread_ids; finalize without
                # the duplicate db.delete below.
                pending.status = PendingActionStatus.COMPLETED
                pending.completed_at = datetime.now(UTC)
                return
            # Image deletes need to drop the storage blob alongside the DB row;
            # the immediate-delete path does the same. An orphaned blob from a
            # storage failure is recoverable; a stuck pending action isn't.
            if pending.action_type == PendingActionType.IMAGE_DELETE:
                import contextlib

                from sheaf.storage import get_storage

                with contextlib.suppress(Exception):
                    await get_storage().delete(target.key)
            await db.delete(target)

    pending.status = PendingActionStatus.COMPLETED
    pending.completed_at = datetime.now(UTC)


async def _target_in_scope(
    target: Any, pending: PendingAction, db: AsyncSession
) -> bool:
    """Verify the target still belongs to the system that queued the action.

    Most targets carry system_id directly. UploadedFile is user-scoped, so
    we resolve through the system's user_id. ContentRevision is polymorphic —
    walk to its target row and read system_id there.
    """
    if isinstance(target, ContentRevision):
        if target.target_type == "journal_entry":
            entry = await db.get(JournalEntry, target.target_id)
            return entry is not None and entry.system_id == pending.system_id
        if target.target_type == "member_bio":
            member = await db.get(Member, target.target_id)
            return member is not None and member.system_id == pending.system_id
        if target.target_type == "message":
            msg = await db.get(Message, target.target_id)
            return msg is not None and msg.system_id == pending.system_id
        return False
    if isinstance(target, NotificationChannel):
        # Channels don't carry system_id directly; resolve via watch token.
        token = await db.get(WatchToken, target.watch_token_id)
        return token is not None and token.system_id == pending.system_id
    if hasattr(target, "system_id"):
        return target.system_id == pending.system_id
    if hasattr(target, "user_id"):
        system = await db.get(System, pending.system_id)
        return system is not None and target.user_id == system.user_id
    return False


# ---------------------------------------------------------------------------
# Finalize safety-setting changes
# ---------------------------------------------------------------------------


async def finalize_safety_change(
    request: SafetyChangeRequest, db: AsyncSession
) -> None:
    """Apply a deferred safety-setting loosening to the system."""
    system = await db.get(System, request.system_id)
    if system is not None:
        for field, value in (request.changes or {}).items():
            if hasattr(system, field):
                setattr(system, field, value)
    request.status = SafetyChangeStatus.COMPLETED
    request.completed_at = datetime.now(UTC)
