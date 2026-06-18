"""Admin action audit log.

Every privileged admin action writes a row here so:

  - Operators have an append-only trail of who did what when, with
    optional before/after JSON snapshots for state-changing actions.
  - Affected users can see what an admin did to their account via
    `/v1/auth/admin-activity` — accountability, not just trust-us.

The table is append-only by design. There is no UPDATE / DELETE
endpoint; rows are kept indefinitely. If an admin user is removed the
FK is SET NULL so history survives, but the row preserves the action
context (which can still be attributed via the historical email if
the operator captured it in before_json).

**What we log:** state-changing admin actions (user_update, approve,
reject, member-limit, safety-reset, pending-bypass) and unusual
privacy-sensitive reads (import-log views; future additions like
dossier exports as those land). What we DON'T log: routine reads
like the admin user list, single-user detail views, or search.
Logging every browse would swamp the table and actively hurt the
abuse-detection use case — a malicious admin browsing user data
wouldn't stand out against the noise floor. Mutation-only is the
signal-rich shape.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, UUIDMixin
from sheaf.models.types import InetStr


class AdminAuditAction(enum.StrEnum):
    """Enumerated admin actions. Extend when new admin endpoints land."""

    USER_UPDATE = "user_update"          # PATCH /v1/admin/users/{id} — tier, flags, member_limit
    USER_APPROVE = "user_approve"        # registration approval
    USER_REJECT = "user_reject"          # registration rejection
    USER_MEMBER_LIMIT_SET = "user_member_limit_set"
    USER_SAFETY_RESET = "user_safety_reset"        # PR 2: drop safeguards forward
    USER_PENDING_BYPASS = "user_pending_bypass"    # PR 2: finalise all pending actions now
    IMPORT_LOG_VIEW = "import_log_view"            # PR 2: viewing an import job log
    USER_SESSION_REVOKE = "user_session_revoke"    # PR 3: terminate a single session
    USER_API_KEYS_ROTATE_ALL = "user_api_keys_rotate_all"  # PR 3: revoke every API key
    USER_SUSPEND = "user_suspend"                  # PR 4: soft-ban with or without expiry
    USER_UNSUSPEND = "user_unsuspend"              # PR 4: lift soft-ban (admin OR sweep)
    USER_DOSSIER_EXPORT = "user_dossier_export"    # PR 4: GDPR Article 15 metadata bundle
    USER_BAN = "user_ban"                          # PR 5: permanent ban (BANNED state)
    USER_UNBAN = "user_unban"                      # PR 5: lift permanent ban
    USER_PASSWORD_RESET = "user_password_reset"    # recovery cluster
    USER_EMAIL_CHANGE = "user_email_change"        # recovery cluster
    USER_TOTP_DISABLE = "user_totp_disable"        # recovery cluster
    USER_EMAIL_VERIFY = "user_email_verify"        # recovery cluster
    USER_DELETION_CANCEL = "user_deletion_cancel"  # recovery cluster
    INVITE_CREATE = "invite_create"
    INVITE_DELETE = "invite_delete"
    JOB_TRIGGER = "job_trigger"                    # manual job / maintenance runs
    SECURITY_IP_LOOKUP = "security_ip_lookup"      # searched security log by IP/subnet
    SECURITY_HISTORY_VIEW = "security_history_view"  # viewed one account's security events


class AdminAuditTargetType(enum.StrEnum):
    """What kind of object the action operated on."""

    USER = "user"
    SYSTEM = "system"
    PENDING_ACTION = "pending_action"
    IMPORT_JOB = "import_job"
    INVITE = "invite"
    JOB = "job"


class AdminAuditEvent(UUIDMixin, Base):
    __tablename__ = "admin_audit_events"

    # Admin who took the action. SET NULL so we keep history even if
    # the admin's account is later removed — the before_json should
    # have captured an identifier (email/id) at action time for
    # post-deletion attribution.
    admin_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    action: Mapped[AdminAuditAction] = mapped_column(
        # `name=...` must match the type created by the migration,
        # otherwise SQLAlchemy autoderives `adminauditaction` from the
        # class name and INSERT fails with "type does not exist".
        Enum(
            AdminAuditAction,
            name="admin_audit_action",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        index=True,
    )

    target_type: Mapped[AdminAuditTargetType] = mapped_column(
        Enum(
            AdminAuditTargetType,
            name="admin_audit_target_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        index=True,
    )

    # The specific object identifier (e.g. user.id, pending_action.id).
    # Not a hard FK so deleted targets don't strand the history.
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )

    # Convenience denormalisation: the user whose ACCOUNT was affected.
    # Usually == target_id when target_type is USER; for nested targets
    # (pending_action, import_job) it's the owning user. Lets the
    # "admin activity on my account" view fast-filter without joins.
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Required for destructive / irreversible actions, optional for
    # routine ones. Surfaced verbatim to both admin viewers and
    # affected users.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # State snapshots. For USER_UPDATE the diff is the only useful
    # record (e.g. tier free -> plus). NULL is allowed when the
    # action doesn't naturally have a before/after.
    before_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    # The admin's email at the time of the action, captured here so
    # row-level reads don't need to join + decrypt the users table.
    # NOT a privacy regression — admin emails are already exposed via
    # the admin users listing.
    admin_email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Where the admin acted from. Populated from the request-scoped
    # client IP (see request_context) so an action from an unexpected
    # origin is visible. INET for subnet queries; NULL if unresolved
    # (e.g. an internal/job-triggered action with no request).
    ip: Mapped[str | None] = mapped_column(InetStr, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
