"""Admin audit-log read endpoints.

Two surfaces:

  - `GET /v1/admin/audit-events` — admin-only, paginated, filterable.
    For operators reviewing what their fellow admins have been up to.
  - `GET /v1/auth/admin-activity` — self-only, returns rows where
    target_user_id == the caller. The transparency layer: every user
    can see what admins have done to their account, with the admin's
    email and the reason text. Pairs with the published admin-panel
    documentation (planned) so the data on offer is auditable from
    the outside.

Writes happen via `sheaf.services.admin_audit.log_admin_action`,
called inline from each admin endpoint that mutates state. There is
no write endpoint here — the log is append-only by callsite, not by
external API.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user, get_current_user
from sheaf.database import get_db
from sheaf.models.admin_audit_event import AdminAuditEvent
from sheaf.models.user import User
from sheaf.schemas.admin_audit import AdminAuditEventRead, UserAdminActivityRead

router = APIRouter(tags=["admin audit"])


@router.get(
    "/admin/audit-events",
    response_model=list[AdminAuditEventRead],
)
async def list_admin_audit_events(
    target_user_id: uuid.UUID | None = None,
    admin_user_id: uuid.UUID | None = None,
    action: str | None = None,
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> list[AdminAuditEvent]:
    """Paginated admin audit log. Filters on target user, acting
    admin, or action type — all optional. Most-recent first."""
    stmt = select(AdminAuditEvent).order_by(
        desc(AdminAuditEvent.created_at),
        # Tiebreaker: id desc so same-second rows have a stable order
        # across page boundaries.
        desc(AdminAuditEvent.id),
    )
    if target_user_id is not None:
        stmt = stmt.where(AdminAuditEvent.target_user_id == target_user_id)
    if admin_user_id is not None:
        stmt = stmt.where(AdminAuditEvent.admin_user_id == admin_user_id)
    if action is not None:
        stmt = stmt.where(AdminAuditEvent.action == action)
    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


@router.get(
    "/admin/audit-events/{event_id}",
    response_model=AdminAuditEventRead,
)
async def get_admin_audit_event(
    event_id: uuid.UUID,
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
) -> AdminAuditEvent:
    event = await db.get(AdminAuditEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Audit event not found",
        )
    return event


@router.get(
    "/auth/admin-activity",
    response_model=list[UserAdminActivityRead],
)
async def list_admin_activity_on_self(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[AdminAuditEvent]:
    """List admin actions taken against the caller's account.

    Returns rows where `target_user_id == self.id`. Visible to every
    authenticated user with no admin gate — this is the user-facing
    transparency layer over the audit log.
    """
    stmt = (
        select(AdminAuditEvent)
        .where(AdminAuditEvent.target_user_id == user.id)
        .order_by(
            desc(AdminAuditEvent.created_at),
            desc(AdminAuditEvent.id),
        )
        .offset((page - 1) * limit)
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
