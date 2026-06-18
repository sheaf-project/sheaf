"""Admin search over the security-event log.

Three tools, all backed by the `security_events` table:

  - POST /admin/security/ip-lookup    everything from an IP / subnet
  - GET  /admin/security/stuffing     top failing IPs (credential stuffing)
  - POST /admin/users/{id}/security-events   one account's auth timeline

The two reads that target a specific IP or account expose user activity
+ IP, so they are write-gated, require a reason, and write an admin audit
row - same treatment as dossier export. The stuffing view is an
aggregate over IPs (no single user's data), so it follows the
non-audited operational-read convention (like the rate-limit history).
"""

from __future__ import annotations

import ipaddress
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user, get_admin_write_user
from sheaf.database import get_db
from sheaf.models.admin_audit_event import AdminAuditAction, AdminAuditTargetType
from sheaf.models.security_event import SecurityEvent
from sheaf.models.user import User
from sheaf.services.admin_audit import log_admin_action
from sheaf.services.security_events import (
    accounts_seen_from_ip,
    events_for_ip,
    events_for_user,
    stuffing_offenders,
)

router = APIRouter(prefix="/admin", tags=["admin security"])


class AdminReasonBody(BaseModel):
    """Required free-form reason captured on the audit row."""

    reason: str = Field(min_length=1, max_length=500)


class IpLookupBody(AdminReasonBody):
    # Exact IP ("203.0.113.7") or CIDR subnet ("203.0.113.0/24").
    target: str = Field(min_length=1, max_length=64)


class SecurityEventRow(BaseModel):
    id: uuid.UUID
    created_at: datetime
    event_type: str
    outcome: str
    user_id: uuid.UUID | None
    ip: str | None
    user_agent: str | None
    detail: dict | None


def _row(e: SecurityEvent) -> SecurityEventRow:
    return SecurityEventRow(
        id=e.id,
        created_at=e.created_at,
        event_type=str(e.event_type),
        outcome=e.outcome,
        user_id=e.user_id,
        ip=str(e.ip) if e.ip is not None else None,
        user_agent=e.user_agent,
        detail=e.detail,
    )


class IpLookupResponse(BaseModel):
    query: str
    is_subnet: bool
    event_count: int
    events: list[SecurityEventRow]
    # Distinct known accounts that appear in events from here - the
    # ban-evasion / shared-IP signal.
    distinct_account_ids: list[uuid.UUID]
    # Accounts whose recorded signup IP is exactly this address. Only
    # populated for exact-IP queries: signup_ip is a plain string column,
    # so it can't be subnet-matched.
    signup_match_ids: list[uuid.UUID]
    note: str


class StuffingRow(BaseModel):
    ip: str
    distinct_accounts: int
    failures: int
    last_seen: datetime


class StuffingResponse(BaseModel):
    since: datetime
    window_hours: int
    min_failures: int
    offenders: list[StuffingRow]


class UserSecurityHistoryResponse(BaseModel):
    user_id: uuid.UUID
    event_count: int
    events: list[SecurityEventRow]


def _parse_target(target: str) -> tuple[str, bool]:
    """Validate the lookup target and report whether it's a subnet.

    Rejecting non-IP input here keeps malformed values from reaching the
    INET cast (which would 500) and bounds what the query can express.
    """
    if "/" in target:
        try:
            ipaddress.ip_network(target, strict=False)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid CIDR subnet",
            ) from exc
        return target, True
    try:
        ipaddress.ip_address(target)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid IP address",
        ) from exc
    return target, False


@router.post("/security/ip-lookup", response_model=IpLookupResponse)
async def ip_lookup(
    body: IpLookupBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Everything the security log knows about an IP or subnet."""
    target, is_subnet = _parse_target(body.target)

    events = await events_for_ip(db, target)
    account_ids = await accounts_seen_from_ip(db, target)

    signup_match_ids: list[uuid.UUID] = []
    if not is_subnet:
        result = await db.execute(
            select(User.id).where(User.signup_ip == target)
        )
        signup_match_ids = list(result.scalars().all())

    note = (
        "Live sessions and trusted-device IPs are not searchable by IP "
        "(session IPs live in Redis without an IP index); this covers the "
        "security-event log plus exact signup-IP matches."
    )

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.SECURITY_IP_LOOKUP,
        target_type=AdminAuditTargetType.SYSTEM,
        reason=body.reason,
        after={
            "query": target,
            "is_subnet": is_subnet,
            "event_count": len(events),
            "distinct_accounts": len(account_ids),
        },
    )
    await db.commit()

    return IpLookupResponse(
        query=target,
        is_subnet=is_subnet,
        event_count=len(events),
        events=[_row(e) for e in events],
        distinct_account_ids=account_ids,
        signup_match_ids=signup_match_ids,
        note=note,
    )


@router.get("/security/stuffing", response_model=StuffingResponse)
async def stuffing(
    hours: int = Query(default=24, ge=1, le=720),
    min_failures: int = Query(default=10, ge=1, le=10_000),
    limit: int = Query(default=50, ge=1, le=500),
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Top IPs by failed-login volume - the credential-stuffing view.

    Ordered by distinct accounts targeted (the stuffing fingerprint),
    then raw failures (which surfaces single-account brute force and
    unknown-user scanning too).
    """
    since = datetime.now(UTC) - timedelta(hours=hours)
    offenders = await stuffing_offenders(
        db, since=since, min_failures=min_failures, limit=limit
    )
    return StuffingResponse(
        since=since,
        window_hours=hours,
        min_failures=min_failures,
        offenders=[StuffingRow(**o) for o in offenders],
    )


@router.post(
    "/users/{user_id}/security-events",
    response_model=UserSecurityHistoryResponse,
)
async def user_security_events(
    user_id: uuid.UUID,
    body: AdminReasonBody,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """One account's security timeline, newest first."""
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    events = await events_for_user(db, user_id)

    await log_admin_action(
        db,
        admin=admin,
        action=AdminAuditAction.SECURITY_HISTORY_VIEW,
        target_type=AdminAuditTargetType.USER,
        target_id=user_id,
        target_user_id=user_id,
        reason=body.reason,
        after={"event_count": len(events)},
    )
    await db.commit()

    return UserSecurityHistoryResponse(
        user_id=user_id,
        event_count=len(events),
        events=[_row(e) for e in events],
    )
