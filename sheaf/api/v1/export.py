"""Article 20 (data portability) export.

Returns the user's plural-system content in a structured, re-importable
JSON format. Account/identity data + server-derived telemetry (sessions,
API key audit, IPs) live in the Article 15 endpoint instead - they
shouldn't ride along when someone shares their export with another
person or imports it into a different Sheaf instance.

Sync `GET /export` returns JSON-only and is fast enough for inline use.
Async `POST /export/jobs` enqueues a build that includes image bytes,
delivered as a zip via S3-presigned download or filesystem stream.
"""

import logging
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user
from sheaf.auth.lockout import ensure_not_locked, record_login_failure
from sheaf.auth.passwords import verify_password
from sheaf.auth.totp import TotpCheck, check_code_once, totp_error_detail
from sheaf.config import settings
from sheaf.crypto import decrypt
from sheaf.database import get_db
from sheaf.middleware.rate_limit import rate_limit
from sheaf.models.activity_event import ActivityAction
from sheaf.models.content_revision import ContentRevision, ContentRevisionTarget
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.export_job import ExportJob, ExportJobStatus
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.journal_entry import JournalEntry
from sheaf.models.member import Member
from sheaf.models.notification_channel import NotificationChannel
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.uploaded_file import UploadedFile
from sheaf.models.user import User
from sheaf.models.watch_token import WatchToken
from sheaf.services import export_storage
from sheaf.services.activity_log import log_activity
from sheaf.services.custom_fields import field_value_plaintext
from sheaf.services.journals import entry_plaintext, revision_plaintext
from sheaf.services.members import member_plaintext
from sheaf.services.openplural_archive import unpack_residual

router = APIRouter(prefix="/export", tags=["export"])

logger = logging.getLogger("sheaf.export")


def _safe_decrypt(value: str | None) -> str | None:
    """Decrypt an encrypted field for the export, tolerating a single
    unreadable value instead of sinking the whole dump.

    A data export is best-effort: if one field holds ciphertext that can't
    be decrypted (corruption, a key-rotation edge, or plain bad data), that
    one field should come back null while every other field still exports -
    far better than 500ing the entire account backup over a single row.
    Returns None for a None input, or None when decryption fails. The
    warning lets an operator see it happened without leaking the field's
    plaintext or ciphertext into the logs.
    """
    if value is None:
        return None
    try:
        return decrypt(value)
    except Exception:
        logger.warning(
            "An encrypted field could not be decrypted during export; "
            "exporting it as null"
        )
        return None


# Stand-in written into the export when a required text field (e.g. a member
# name) can't be decrypted. Null would risk a NOT NULL violation on re-import;
# a placeholder re-imports cleanly and signals the field was unrecoverable.
_UNREADABLE_PLACEHOLDER = "[unreadable]"


def _safe_plaintext(fn: Callable, *, fallback):
    """Run a shared decrypt-to-plaintext helper while assembling the export,
    tolerating a single unreadable field.

    The export leans on shared service helpers (member / journal / revision /
    custom-field plaintext) that raise on unreadable ciphertext. Left bare
    that would 500 the entire export over one bad row. We can't soften those
    helpers - they back read endpoints too - so we catch here, at the export
    call site only, log a content-free warning, and fall back: a placeholder
    for required fields, null for optional ones. The fallback shape mirrors
    the helper's own return (a tuple for the two-value helpers).
    """
    try:
        return fn()
    except Exception:
        logger.warning(
            "An encrypted field could not be decrypted during export; "
            "substituting a fallback value"
        )
        return fallback


# asyncpg caps a single statement at 32767 bind parameters. Any `.in_(ids)`
# over a user-scaled id list can blow past that on a large account (a hard
# 500), so we batch such lists into chunks well under the limit and merge.
_BIND_PARAM_CHUNK = 1000


def _chunked(items: list, size: int = _BIND_PARAM_CHUNK) -> Iterator[list]:
    """Yield `items` in slices of at most `size`. Empty input yields nothing."""
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def _count_where(db: AsyncSession, model, condition) -> int:
    """Cheap COUNT(*) for `model` rows matching `condition`."""
    return int(
        (
            await db.execute(select(func.count()).select_from(model).where(condition))
        ).scalar_one()
    )


async def _enforce_sync_export_size(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Refuse the sync export path for accounts too large to assemble in
    memory on the event loop, pointing the caller at the async job flow.

    A FastAPI route dependency rather than an inline check: it must guard the
    HTTP endpoint only. The async export builder calls export_all() directly
    as a function (not over HTTP), so it deliberately bypasses this guard -
    the whole point is that oversized accounts go through that streaming
    path instead.

    Cheap COUNTs first (do not load-then-check). Counts the system-scoped
    tables that dominate the in-memory payload; revisions/files/etc. scale
    with these, so bounding them bounds the whole dump. get_current_user /
    get_db are cached within the request, so this shares the handler's
    session and user - no duplicate auth or connection.
    """
    threshold = settings.export_sync_max_rows
    if threshold <= 0:
        return  # guard disabled

    system_id = (
        await db.execute(select(System.id).where(System.user_id == user.id))
    ).scalar_one_or_none()
    if system_id is None:
        return  # nothing to export

    from sheaf.models.message import Message

    total = 0
    for model in (Member, Front, JournalEntry, Message):
        total += await _count_where(db, model, model.system_id == system_id)
        if total > threshold:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    "Your account is too large to export synchronously. Use "
                    "the async export instead: POST /v1/export/jobs, which "
                    "streams the result to a downloadable file."
                ),
            )


@router.get("", dependencies=[rate_limit(6, 3600, "user"), Depends(_enforce_sync_export_size)])
async def export_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    format: Literal["sheaf", "openplural"] = Query(
        "sheaf",
        description=(
            "Output format. 'sheaf' is the native re-importable JSON; "
            "'openplural' wraps it in an OpenPlural v0.1 envelope "
            "(uri-only assets; use the async zip export for image bytes)."
        ),
    ),
):
    """Export the user's plural-system content as JSON. Article 20 - data
    portability. Re-importable into another Sheaf instance.

    Excludes account-identity data (email, sessions, API keys, IPs) - that
    lives in the separate /v1/account/data endpoint (Article 15).
    """

    # System
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        return _maybe_openplural(_empty_export(), format)

    # Members. Member.name is encrypted ciphertext, so DB-side ORDER BY on
    # it is meaningless - sort in Python after decrypting names below.
    members_result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    # member_plaintext returns (name, description). Name is required (a
    # placeholder on failure so re-import doesn't hit the NOT NULL column);
    # description is optional (null). A single unreadable member field can't
    # be more granular here - the helper decrypts both together - so if
    # either raises, name falls back to the placeholder and description to
    # null, and the rest of the export is unaffected.
    members_with_plaintext = [
        (
            m,
            *_safe_plaintext(
                lambda m=m: member_plaintext(m),
                fallback=(_UNREADABLE_PLACEHOLDER, None),
            ),
        )
        for m in members_result.scalars().all()
    ]
    members_with_plaintext.sort(key=lambda t: t[1].casefold())

    # Fronts with members
    fronts_result = await db.execute(
        select(Front)
        .options(selectinload(Front.members))
        .where(Front.system_id == system.id)
        .order_by(Front.started_at.desc())
    )
    fronts = fronts_result.scalars().all()

    # Groups with members
    groups_result = await db.execute(
        select(Group)
        .options(selectinload(Group.members))
        .where(Group.system_id == system.id)
    )
    groups = groups_result.scalars().all()

    # Tags with members
    tags_result = await db.execute(
        select(Tag)
        .options(selectinload(Tag.members))
        .where(Tag.system_id == system.id)
    )
    tags = tags_result.scalars().all()

    # Custom fields + values
    fields_result = await db.execute(
        select(CustomFieldDefinition)
        .options(selectinload(CustomFieldDefinition.values))
        .where(CustomFieldDefinition.system_id == system.id)
    )
    fields = fields_result.scalars().all()

    # Journal entries (encrypted at rest; decrypt for export)
    journals_result = await db.execute(
        select(JournalEntry)
        .where(JournalEntry.system_id == system.id)
        .order_by(JournalEntry.created_at.asc())
    )
    journal_entries = list(journals_result.scalars().all())

    # Content revisions: bio (member.id) + journal-entry (entry.id) edit
    # history. Polymorphic, so we filter by target_id IN ({member_ids,
    # journal_ids}) - much cheaper than a per-row lookup.
    member_id_set = {m.id for m, *_ in members_with_plaintext}
    journal_id_set = {e.id for e in journal_entries}
    targets = list(member_id_set | journal_id_set)
    # Batch the target-id IN() over chunks so a large account can't exceed
    # asyncpg's 32767 bind-param ceiling (which would 500 the whole export).
    # Chunking loses the single query's global ORDER BY, so re-sort the
    # merged batches by created_at to preserve the export's ordering.
    revisions = []
    for chunk in _chunked(targets):
        revisions_result = await db.execute(
            select(ContentRevision)
            .where(ContentRevision.target_id.in_(chunk))
            .where(
                ContentRevision.target_type.in_(
                    [
                        ContentRevisionTarget.MEMBER_BIO.value,
                        ContentRevisionTarget.JOURNAL_ENTRY.value,
                    ]
                )
            )
            .order_by(ContentRevision.created_at.asc())
        )
        revisions.extend(revisions_result.scalars().all())
    revisions.sort(key=lambda r: r.created_at)

    # Watch tokens + their channels - owner-side notification config the
    # user explicitly built up. Re-importable in the sense that the
    # filter/trigger/payload/delivery shaping config carries over to
    # another instance; per-channel destination state (recipient
    # subscriptions, activation hashes, last_delivered_at) doesn't and
    # is omitted. Webhook secrets live in a separate encrypted column
    # and are also omitted.
    tokens_result = await db.execute(
        select(WatchToken)
        .options(
            selectinload(WatchToken.channels).selectinload(
                NotificationChannel.group_rules
            ),
            selectinload(WatchToken.channels).selectinload(
                NotificationChannel.member_rules
            ),
        )
        .where(WatchToken.system_id == system.id)
        .order_by(WatchToken.created_at.asc())
    )
    watch_tokens = list(tokens_result.scalars().all())

    # File inventory - image bytes themselves don't ride along with the
    # sync export (use the async with-images job for that), but the
    # metadata IS portable user data and should be in the Article 20
    # dump so re-import can know "you had these files" even if it
    # can't restore them.
    files_result = await db.execute(
        select(UploadedFile)
        .where(UploadedFile.user_id == user.id)
        .order_by(UploadedFile.created_at.asc())
    )
    uploaded_files = list(files_result.scalars().all())

    # Reminders - config the user explicitly set up. Title and body are
    # encrypted at rest; we decrypt for the export so the user has the
    # plaintext. Pending queue rows are runtime state and not exported.
    from sheaf.models.reminder import Reminder

    reminders_result = await db.execute(
        select(Reminder)
        .options(selectinload(Reminder.scope_members))
        .where(Reminder.system_id == system.id)
        .order_by(Reminder.created_at.asc())
    )
    reminders = list(reminders_result.scalars().all())

    # Polls - same encryption discipline as reminders. We export both
    # current vote rows and the audit log, since the audit is part of
    # what makes the poll legible after the fact.
    from sheaf.models.poll import Poll

    polls_result = await db.execute(
        select(Poll)
        .options(
            selectinload(Poll.options),
            selectinload(Poll.votes),
            selectinload(Poll.events),
        )
        .where(Poll.system_id == system.id)
        .order_by(Poll.created_at.asc())
    )
    polls = list(polls_result.scalars().all())

    # Messages - boards + threads. Body is decrypted; deleted messages
    # excluded (those carry no remaining content). Revisions ride the
    # existing content_revisions surface and aren't dumped here per the
    # same shape as journals.
    from sheaf.models.message import Message

    msgs_result = await db.execute(
        select(Message)
        .where(Message.system_id == system.id, Message.deleted_at.is_(None))
        .order_by(Message.created_at.asc())
    )
    messages_rows = list(msgs_result.scalars().all())

    native = {
        "version": "2",
        "system": _system_dict(system),
        "members": [
            {
                "id": str(m.id),
                "name": name,
                "display_name": m.display_name,
                "description": description,
                "pronouns": m.pronouns,
                "avatar_url": m.avatar_url,
                "banner_url": m.banner_url,
                "color": m.color,
                "birthday": m.birthday,
                "pluralkit_id": m.pluralkit_id,
                "emoji": m.emoji,
                "is_custom_front": m.is_custom_front,
                "privacy": m.privacy.value,
                "note": _safe_decrypt(m.note) if m.note else None,
                "quick_switch_pin": m.quick_switch_pin,
                "notify_on_front_global": m.notify_on_front_global,
                "notify_on_front_self": m.notify_on_front_self,
                "notify_on_front_member_ids": m.notify_on_front_member_ids,
                "created_at": m.created_at.isoformat(),
                "archived_at": (
                    m.archived_at.isoformat() if m.archived_at else None
                ),
            }
            for (m, name, description) in members_with_plaintext
        ],
        "fronts": [
            {
                "id": str(f.id),
                "started_at": f.started_at.isoformat(),
                "ended_at": f.ended_at.isoformat() if f.ended_at else None,
                "member_ids": [str(m.id) for m in f.members],
                "custom_status": (
                    _safe_decrypt(f.custom_status) if f.custom_status else None
                ),
            }
            for f in fronts
        ],
        "groups": [
            {
                "id": str(g.id),
                "name": g.name,
                "description": g.description,
                "color": g.color,
                "parent_id": str(g.parent_id) if g.parent_id else None,
                "member_ids": [str(m.id) for m in g.members],
            }
            for g in groups
        ],
        "tags": [
            {
                "id": str(t.id),
                "name": t.name,
                "color": t.color,
                "member_ids": [str(m.id) for m in t.members],
            }
            for t in tags
        ],
        "custom_fields": [
            {
                "id": str(fd.id),
                "name": fd.name,
                "field_type": fd.field_type.value,
                "options": fd.options,
                "order": fd.order,
                "privacy": fd.privacy.value,
                "values": [
                    {
                        "member_id": str(v.member_id),
                        # Custom-field value is optional user content: null on
                        # an unreadable field rather than sinking the export.
                        "value": _safe_plaintext(
                            lambda v=v: field_value_plaintext(v), fallback=None
                        ),
                    }
                    for v in fd.values
                ],
            }
            for fd in fields
        ],
        "journals": [_journal_dict(e) for e in journal_entries],
        "revisions": [_revision_dict(r) for r in revisions],
        "watch_tokens": [_watch_token_dict(t) for t in watch_tokens],
        "uploaded_files": [_uploaded_file_dict(f) for f in uploaded_files],
        "reminders": [_reminder_dict(r) for r in reminders],
        "polls": [_poll_dict(p) for p in polls],
        "messages": [_message_dict(m) for m in messages_rows],
    }
    return _maybe_openplural(native, format)


def _maybe_openplural(native: dict, format: str) -> dict:
    """Return the native dict unchanged, or wrap it in an OpenPlural v0.1
    envelope when `format == "openplural"`.

    The sync path produces uri-only assets (no image bytes); the async
    zip builder calls `openplural_export.build_envelope` directly with
    `include_asset_bytes=True` for the bundle.
    """
    if format != "openplural":
        return native
    from sheaf.services.openplural_export import build_envelope

    return build_envelope(native, exported_at=datetime.now(UTC).isoformat())


def _empty_export() -> dict:
    return {
        "version": "2",
        "system": None,
        "members": [],
        "fronts": [],
        "groups": [],
        "tags": [],
        "custom_fields": [],
        "journals": [],
        "revisions": [],
        "watch_tokens": [],
        "uploaded_files": [],
        "reminders": [],
        "polls": [],
        "messages": [],
    }


def _system_dict(system: System) -> dict:
    return {
        "id": str(system.id),
        "name": system.name,
        "description": system.description,
        "note": _safe_decrypt(system.note) if system.note else None,
        "tag": system.tag,
        "avatar_url": system.avatar_url,
        "color": system.color,
        "privacy": system.privacy.value,
        # User-set system preferences. Re-import should restore these.
        "replace_fronts_default": system.replace_fronts_default,
        "coalesce_contiguous_fronts": system.coalesce_contiguous_fronts,
        "date_format": system.date_format.value,
        # NULL (auto) round-trips as null; a set zone as its IANA name.
        "timezone": system.timezone,
        "delete_confirmation": system.delete_confirmation.value,
        # System Safety: per-category toggles + grace period + auto-pin.
        "safety": {
            "grace_period_days": system.safety_grace_period_days,
            "applies_to_members": system.safety_applies_to_members,
            "applies_to_groups": system.safety_applies_to_groups,
            "applies_to_tags": system.safety_applies_to_tags,
            "applies_to_fields": system.safety_applies_to_fields,
            "applies_to_fronts": system.safety_applies_to_fronts,
            "applies_to_journals": system.safety_applies_to_journals,
            "applies_to_images": system.safety_applies_to_images,
            "applies_to_revisions": system.safety_applies_to_revisions,
            "applies_to_notifications": system.safety_applies_to_notifications,
            "applies_to_reminders": system.safety_applies_to_reminders,
            "applies_to_polls": system.safety_applies_to_polls,
            "applies_to_messages": system.safety_applies_to_messages,
            "applies_to_archive": system.safety_applies_to_archive,
            "auto_pin_first_revision": system.auto_pin_first_revision,
        },
        "retention": {
            "journal_max_revisions": system.journal_max_revisions,
            "journal_max_revision_days": system.journal_max_revision_days,
            "pinned_revision_max_per_target": system.pinned_revision_max_per_target,
        },
        # OpenPlural import residual (foreign data Sheaf cannot model),
        # decrypted to a plain dict so it rides the portability export and
        # is re-merged on a Sheaf OpenPlural export. None when empty.
        "openplural_archive": unpack_residual(system.openplural_archive) or None,
    }


def _journal_dict(entry: JournalEntry) -> dict:
    # Journal title and body are optional content: null on an unreadable
    # field rather than 500ing the whole export.
    title, body = _safe_plaintext(
        lambda: entry_plaintext(entry), fallback=(None, None)
    )
    return {
        "id": str(entry.id),
        "member_id": str(entry.member_id) if entry.member_id else None,
        "title": title,
        "body": body,
        "visibility": entry.visibility,
        "author_user_id": (
            str(entry.author_user_id) if entry.author_user_id else None
        ),
        "author_member_ids": entry.author_member_ids,
        "author_member_names": entry.author_member_names,
        "image_keys": entry.image_keys,
        "created_at": entry.created_at.isoformat(),
        "updated_at": entry.updated_at.isoformat(),
    }


def _revision_dict(revision: ContentRevision) -> dict:
    # Revision title and body are optional content: null on an unreadable
    # field rather than 500ing the whole export.
    title, body = _safe_plaintext(
        lambda: revision_plaintext(revision), fallback=(None, None)
    )
    return {
        "id": str(revision.id),
        "target_type": revision.target_type,
        "target_id": str(revision.target_id),
        "user_id": str(revision.user_id) if revision.user_id else None,
        "editor_member_ids": revision.editor_member_ids,
        "editor_member_names": revision.editor_member_names,
        "title": title,
        "body": body,
        "image_keys": revision.image_keys,
        "pinned_at": revision.pinned_at.isoformat() if revision.pinned_at else None,
        "created_at": revision.created_at.isoformat(),
    }


def _watch_token_dict(token: WatchToken) -> dict:
    return {
        "id": str(token.id),
        "label": token.label,
        "revoked_at": token.revoked_at.isoformat() if token.revoked_at else None,
        "created_at": token.created_at.isoformat(),
        "channels": [_channel_dict(c) for c in token.channels],
    }


def _channel_dict(channel: NotificationChannel) -> dict:
    """Owner-side channel config minus per-instance state.

    Omitted because they don't survive a re-import:
    - Activation hashes / management token hash / activation expiry
    - destination_state, redeemed_at, redeemed_by_account_id
    - last_delivered_at
    - webhook_secret_encrypted (lives in a separate column; would need
      to be re-entered by the owner on a new instance anyway)

    destination_config is included verbatim - it may carry a recipient's
    push endpoint or a webhook URL, all of which the owner already has
    in some external system; the secret bits (BYO Pushover app_token,
    ntfy auth header) are user-set credentials that the owner controls
    and would want to preserve in their own backup.
    """
    return {
        "id": str(channel.id),
        "watch_token_id": str(channel.watch_token_id),
        "name": channel.name,
        "destination_type": channel.destination_type,
        "destination_config": dict(channel.destination_config or {}),
        "event_type": channel.event_type,
        "base_all_members": channel.base_all_members,
        "base_include_private": channel.base_include_private,
        "trigger_on_start": channel.trigger_on_start,
        "trigger_on_stop": channel.trigger_on_stop,
        "trigger_on_cofront_change": channel.trigger_on_cofront_change,
        "cofront_redaction": channel.cofront_redaction,
        "payload_sensitivity": channel.payload_sensitivity,
        "debounce_seconds": channel.debounce_seconds,
        "aggregation_window_seconds": channel.aggregation_window_seconds,
        "quiet_hours": channel.quiet_hours,
        "group_rules": [
            {
                "group_id": str(r.group_id),
                "rule": r.rule,
                "include_private": r.include_private,
            }
            for r in channel.group_rules
        ],
        "member_rules": [
            {"member_id": str(r.member_id), "rule": r.rule}
            for r in channel.member_rules
        ],
        "created_at": channel.created_at.isoformat(),
    }


def _uploaded_file_dict(f: UploadedFile) -> dict:
    """File-inventory metadata - bytes themselves don't ride along with
    the sync export. The async with-images job bundles them, and the
    archive importer restores them; listed here so a plain-JSON
    re-import still knows which files existed."""
    return {
        "id": str(f.id),
        "key": f.key,
        "purpose": f.purpose,
        "size_bytes": f.size_bytes,
        "content_type": f.content_type,
        "created_at": f.created_at.isoformat(),
    }


def _reminder_dict(reminder) -> dict:
    """Reminder config the user explicitly built up. Title and body are
    decrypted to plaintext for the export. Pending-queue rows are
    transient runtime state and not included.

    Re-importable in the sense that the trigger config and channel
    reference carry over to another instance; runtime state (last_fired_at,
    pending queue) is omitted and the channel_id is just the original UUID
    so a re-import on a fresh instance won't resolve unless the channels
    were imported there too."""
    title = _safe_decrypt(reminder.title) if reminder.title else ""
    body = _safe_decrypt(reminder.body) if reminder.body else None
    return {
        "id": str(reminder.id),
        "channel_id": str(reminder.channel_id),
        "name": reminder.name,
        "title": title,
        "body": body,
        "enabled": reminder.enabled,
        "trigger_type": reminder.trigger_type,
        "trigger_member_id": (
            str(reminder.trigger_member_id) if reminder.trigger_member_id else None
        ),
        "trigger_event": reminder.trigger_event,
        "delay_seconds": reminder.delay_seconds,
        "schedule_kind": reminder.schedule_kind,
        "schedule_time": reminder.schedule_time,
        "schedule_dow_mask": reminder.schedule_dow_mask,
        "schedule_dom": reminder.schedule_dom,
        "schedule_tz": reminder.schedule_tz,
        "cron_expression": reminder.cron_expression,
        "scope": reminder.scope,
        "scope_member_ids": [str(m.id) for m in reminder.scope_members],
        "digest_when_absent": reminder.digest_when_absent,
        "created_at": reminder.created_at.isoformat(),
    }


def _message_dict(msg) -> dict:
    """Board message + reply pointer. Body decrypted to plaintext.
    Soft-deleted rows are excluded upstream of this serialiser."""
    return {
        "id": str(msg.id),
        "board_kind": msg.board_kind,
        "board_member_id": (
            str(msg.board_member_id) if msg.board_member_id else None
        ),
        "author_member_id": (
            str(msg.author_member_id) if msg.author_member_id else None
        ),
        "parent_message_id": (
            str(msg.parent_message_id) if msg.parent_message_id else None
        ),
        "body": _safe_decrypt(msg.body) if msg.body else "",
        "created_at": msg.created_at.isoformat(),
        "updated_at": msg.updated_at.isoformat(),
    }


def _poll_dict(poll) -> dict:
    """Poll config + audit trail. Question, description and option
    text are decrypted to plaintext. Vote rows reference member ids and
    option ids (uuids) so a re-import has to round-trip both before
    they're meaningful again."""
    return {
        "id": str(poll.id),
        "question": _safe_decrypt(poll.question) if poll.question else "",
        "description": _safe_decrypt(poll.description) if poll.description else None,
        "kind": poll.kind,
        "results_visibility": poll.results_visibility,
        "closes_at": poll.closes_at.isoformat(),
        "retention_days": poll.retention_days,
        "include_custom_fronts": poll.include_custom_fronts,
        "restrict_voting_to_fronters": poll.restrict_voting_to_fronters,
        "created_at": poll.created_at.isoformat(),
        "options": [
            {
                "id": str(opt.id),
                "text": _safe_decrypt(opt.text) if opt.text else "",
                "position": opt.position,
            }
            for opt in sorted(poll.options, key=lambda o: o.position)
        ],
        "votes": [
            {
                "voted_as_member_id": str(v.voted_as_member_id),
                "option_ids": [str(o) for o in v.option_ids],
                "created_at": v.created_at.isoformat(),
                "updated_at": v.updated_at.isoformat(),
            }
            for v in poll.votes
        ],
        "events": [
            {
                "id": str(e.id),
                "voted_as_member_id": (
                    str(e.voted_as_member_id) if e.voted_as_member_id else None
                ),
                "action": e.action,
                "option_ids": [str(o) for o in e.option_ids],
                "fronting_member_ids": [str(o) for o in e.fronting_member_ids],
                "actor_user_id": (
                    str(e.actor_user_id) if e.actor_user_id else None
                ),
                "created_at": e.created_at.isoformat(),
            }
            for e in sorted(poll.events, key=lambda x: x.created_at)
        ],
    }


# ---------------------------------------------------------------------------
# Async export jobs (with optional image bytes)
# ---------------------------------------------------------------------------


class ExportJobRequest(BaseModel):
    include_images: bool = False
    # Artefact format: "sheaf_native" (export.json + images/) or
    # "openplural" (openplural.json + assets/, an .openplural.zip bundle), or
    # a standalone front-history file ("fronts_csv" / "fronts_json" /
    # "fronts_ics") - a single file of just the fronting history, no zip.
    # include_images is ignored for the fronts_* formats.
    format: Literal[
        "sheaf_native",
        "openplural",
        "fronts_csv",
        "fronts_json",
        "fronts_ics",
    ] = "sheaf_native"
    # Step-up auth: same shape and rules as POST /v1/account/data. Always
    # required; mirrors the Article 15 lock since this is the broader read
    # (everything the user has, including binary blobs).
    password: str
    totp_code: str | None = None


@router.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[rate_limit(10, 3600, "user")],
)
async def create_export_job(
    body: ExportJobRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Enqueue an async export job. Worker builds the zip in the
    background; the user polls or waits for the email/banner.

    Step-up requires password (and TOTP if enrolled) regardless of the
    system's `delete_confirmation` setting - async export is the highest-
    volume read endpoint we have, and the build worker delivers the file
    to whatever session ends up downloading it. Refuses API-key auth so
    a leaked key can't trigger an unattended bulk extraction.
    """
    if getattr(request.state, "auth_method", None) == "api_key":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "API keys cannot trigger data exports. Sign in with a "
                "session or JWT to request one."
            ),
        )

    # Step-up verifies brute-forceable credentials, so it consults and
    # feeds the unified lockout the same way login does.
    ensure_not_locked(user)

    if not await verify_password(body.password, user.password_hash):
        await record_login_failure(db, user)
        # 403: step-up auth denial. See system_safety.verify_destructive_auth
        # for full reasoning.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Password incorrect",
        )
    if user.totp_enabled:
        if not body.totp_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="TOTP code required",
            )
        secret = decrypt(user.totp_secret)
        totp_result = await check_code_once(user.id, secret, body.totp_code)
        if totp_result is not TotpCheck.OK:
            await record_login_failure(db, user, reason="totp_failures")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=totp_error_detail(totp_result),
            )

    # Lock the user row so two concurrent POSTs serialize here; the
    # in-flight check below then can't be raced into a double-insert.
    await db.execute(
        select(User.id).where(User.id == user.id).with_for_update()
    )

    # Per-user concurrency: refuse if there's already a non-terminal job.
    in_flight = await db.execute(
        select(ExportJob).where(
            ExportJob.user_id == user.id,
            ExportJob.status.in_(
                [ExportJobStatus.PENDING, ExportJobStatus.RUNNING]
            ),
        )
    )
    if in_flight.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "You already have an export in progress. Wait for it to "
                "finish before requesting another."
            ),
        )

    job = ExportJob(
        id=uuid.uuid4(),
        user_id=user.id,
        include_images=body.include_images,
        format=body.format,
        status=ExportJobStatus.PENDING,
        requested_at=datetime.now(UTC),
    )
    db.add(job)
    await log_activity(
        db,
        user_id=user.id,
        action=ActivityAction.DATA_EXPORT_REQUESTED,
        target_label=body.format,
    )
    await db.commit()
    await db.refresh(job)
    return _job_to_dict(job)


@router.get("/jobs")
async def list_export_jobs(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List the caller's export jobs, newest first."""
    result = await db.execute(
        select(ExportJob)
        .where(ExportJob.user_id == user.id)
        .order_by(ExportJob.requested_at.desc())
        .limit(50)
    )
    return [_job_to_dict(j) for j in result.scalars().all()]


@router.get("/jobs/{job_id}")
async def get_export_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    job = await _load_owned_job(db, user, job_id)
    return _job_to_dict(job)


@router.get("/jobs/{job_id}/download")
async def download_export_job(
    job_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Stream the export artefact (filesystem) or 302 to a presigned URL
    (S3). Job must belong to the caller and be DONE + not yet expired.

    Download is gated by the normal session/JWT auth - the step-up at
    enqueue time is what protects against drive-by extraction; the
    download itself is just retrieving an already-built artefact.
    """
    job = await _load_owned_job(db, user, job_id)
    if job.status != ExportJobStatus.DONE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Export not available (status: {job.status})",
        )
    if not job.file_location:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Export file no longer available",
        )
    from sheaf.services.front_history_export import (
        FRONT_HISTORY_FORMATS,
        format_extension,
    )

    if job.format == "openplural":
        filename = f"sheaf-export-{job.id}.openplural.zip"
    elif job.format in FRONT_HISTORY_FORMATS:
        filename = (
            f"sheaf-front-history-{job.id}.{format_extension(job.format)}"
        )
    else:
        filename = f"sheaf-export-{job.id}.zip"
    return await export_storage.download_response(job.file_location, filename)


async def _load_owned_job(
    db: AsyncSession, user: User, job_id: uuid.UUID
) -> ExportJob:
    job = await db.get(ExportJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Export job not found"
        )
    return job


def _job_to_dict(job: ExportJob) -> dict:
    return {
        "id": str(job.id),
        "include_images": job.include_images,
        "format": job.format,
        "status": job.status,
        "requested_at": job.requested_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": (
            job.completed_at.isoformat() if job.completed_at else None
        ),
        "expires_at": job.expires_at.isoformat() if job.expires_at else None,
        "file_size_bytes": job.file_size_bytes,
        "error": job.error,
    }
