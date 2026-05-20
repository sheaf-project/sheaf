import json

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.client_settings import ClientSettings
from sheaf.models.user import User

router = APIRouter(prefix="/settings/client", tags=["client-settings"])

# 16 KB max payload
MAX_SETTINGS_BYTES = 16 * 1024


class ClientSettingsBody(BaseModel):
    settings: dict


@router.get("")
async def list_client_settings(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all stored client settings for the current user."""
    result = await db.execute(
        select(ClientSettings)
        .where(ClientSettings.user_id == user.id)
        .order_by(ClientSettings.client_id)
    )
    rows = result.scalars().all()
    return [{"client_id": r.client_id, "settings": r.settings} for r in rows]


@router.get("/{client_id}")
async def get_client_settings(
    client_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get settings for a specific client. Returns 404 if none stored."""
    result = await db.execute(
        select(ClientSettings).where(
            ClientSettings.user_id == user.id,
            ClientSettings.client_id == client_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No settings stored for this client",
        )
    return {"client_id": row.client_id, "settings": row.settings}


@router.put(
    "/{client_id}",
    dependencies=[Depends(require_scope("settings:write"))],
)
async def put_client_settings(
    client_id: str,
    body: ClientSettingsBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Store settings for a specific client. Overwrites any existing settings."""
    if len(client_id) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_id must be 64 characters or fewer",
        )

    # Size check on the serialised JSON
    payload_size = len(json.dumps(body.settings, separators=(",", ":")).encode())
    if payload_size > MAX_SETTINGS_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Settings payload exceeds {MAX_SETTINGS_BYTES // 1024}KB limit",
        )

    result = await db.execute(
        select(ClientSettings).where(
            ClientSettings.user_id == user.id,
            ClientSettings.client_id == client_id,
        )
    )
    row = result.scalar_one_or_none()

    if row is not None:
        row.settings = body.settings
    else:
        row = ClientSettings(
            user_id=user.id,
            client_id=client_id,
            settings=body.settings,
        )
        db.add(row)

    await db.commit()
    await db.refresh(row)
    return {"client_id": row.client_id, "settings": row.settings}


@router.patch(
    "/{client_id}",
    dependencies=[Depends(require_scope("settings:write"))],
)
async def patch_client_settings(
    client_id: str,
    body: ClientSettingsBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Merge the given keys into the stored settings for a client.

    Unlike PUT (which replaces the whole blob), this does an atomic
    top-level key merge in a single statement, so independent callers
    each writing their own key can't clobber one another.
    """
    if len(client_id) > 64:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="client_id must be 64 characters or fewer",
        )

    payload_size = len(json.dumps(body.settings, separators=(",", ":")).encode())
    if payload_size > MAX_SETTINGS_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Settings patch exceeds {MAX_SETTINGS_BYTES // 1024}KB limit",
        )

    insert_stmt = pg_insert(ClientSettings).values(
        user_id=user.id, client_id=client_id, settings=body.settings
    )
    # On conflict, merge: existing JSONB || incoming JSONB (incoming wins
    # per top-level key). The whole thing is one atomic UPDATE.
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_client_settings_user_client",
        set_={
            "settings": ClientSettings.settings.op("||")(
                insert_stmt.excluded.settings
            ),
            "updated_at": func.now(),
        },
    ).returning(ClientSettings.client_id, ClientSettings.settings)

    result = await db.execute(stmt)
    await db.commit()
    row = result.one()
    return {"client_id": row.client_id, "settings": row.settings}


@router.delete(
    "/{client_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_scope("settings:delete"))],
)
async def delete_client_settings(
    client_id: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete stored settings for a specific client."""
    result = await db.execute(
        select(ClientSettings).where(
            ClientSettings.user_id == user.id,
            ClientSettings.client_id == client_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No settings stored for this client",
        )
    await db.delete(row)
    await db.commit()
