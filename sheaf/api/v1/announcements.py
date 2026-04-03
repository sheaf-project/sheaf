import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_admin_user, get_admin_write_user, get_current_user
from sheaf.database import get_db
from sheaf.models.announcement import ServerAnnouncement
from sheaf.models.user import User
from sheaf.schemas.announcement import (
    AnnouncementCreate,
    AnnouncementPublic,
    AnnouncementRead,
    AnnouncementUpdate,
)

# ---------------------------------------------------------------------------
# Admin CRUD
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/admin/announcements", tags=["admin"])


@admin_router.get("", response_model=list[AnnouncementRead])
async def list_all_announcements(
    _: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """List all announcements (including inactive). Requires admin:read."""
    result = await db.execute(
        select(ServerAnnouncement).order_by(ServerAnnouncement.created_at.desc())
    )
    return list(result.scalars().all())


@admin_router.post("", response_model=AnnouncementRead, status_code=status.HTTP_201_CREATED)
async def create_announcement(
    body: AnnouncementCreate,
    admin: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new announcement. Requires admin:write."""
    announcement = ServerAnnouncement(
        title=body.title,
        body=body.body,
        severity=body.severity,
        dismissible=body.dismissible,
        active=body.active,
        created_by=admin.id,
        starts_at=body.starts_at,
        expires_at=body.expires_at,
    )
    db.add(announcement)
    await db.commit()
    await db.refresh(announcement)
    return announcement


@admin_router.patch("/{announcement_id}", response_model=AnnouncementRead)
async def update_announcement(
    announcement_id: uuid.UUID,
    body: AnnouncementUpdate,
    _: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Update an announcement. Requires admin:write."""
    result = await db.execute(
        select(ServerAnnouncement).where(ServerAnnouncement.id == announcement_id)
    )
    announcement = result.scalar_one_or_none()
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")

    if body.title is not None:
        announcement.title = body.title
    if body.body is not None:
        announcement.body = body.body
    if body.severity is not None:
        announcement.severity = body.severity
    if body.dismissible is not None:
        announcement.dismissible = body.dismissible
    if body.active is not None:
        announcement.active = body.active
    if body.clear_starts_at:
        announcement.starts_at = None
    elif body.starts_at is not None:
        announcement.starts_at = body.starts_at
    if body.clear_expires_at:
        announcement.expires_at = None
    elif body.expires_at is not None:
        announcement.expires_at = body.expires_at

    await db.commit()
    await db.refresh(announcement)
    return announcement


@admin_router.delete("/{announcement_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_announcement(
    announcement_id: uuid.UUID,
    _: User = Depends(get_admin_write_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an announcement. Requires admin:write."""
    result = await db.execute(
        select(ServerAnnouncement).where(ServerAnnouncement.id == announcement_id)
    )
    announcement = result.scalar_one_or_none()
    if announcement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Announcement not found")
    await db.delete(announcement)
    await db.commit()


# ---------------------------------------------------------------------------
# Public endpoint — active announcements for authenticated users
# ---------------------------------------------------------------------------

public_router = APIRouter(prefix="/announcements", tags=["announcements"])


@public_router.get("", response_model=list[AnnouncementPublic])
async def get_active_announcements(
    _: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get active announcements. Filters by starts_at/expires_at."""
    now = datetime.now(UTC)
    result = await db.execute(
        select(ServerAnnouncement)
        .where(
            ServerAnnouncement.active == True,  # noqa: E712
        )
        .order_by(
            # Critical first, then warning, then info
            ServerAnnouncement.severity.desc(),
            ServerAnnouncement.created_at.desc(),
        )
    )
    announcements = []
    for a in result.scalars().all():
        if a.starts_at and a.starts_at > now:
            continue
        if a.expires_at and a.expires_at < now:
            continue
        announcements.append(a)
    return announcements
