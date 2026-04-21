import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.schemas.custom_field import (
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
    CustomFieldValueRead,
    CustomFieldValueSet,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.services.system_safety import (
    is_safeguarded,
    queue_pending_action,
    verify_destructive_auth,
)

router = APIRouter(tags=["custom fields"])


async def _get_user_system(user: User, db: AsyncSession) -> System:
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="System not found")
    return system


# --- Field definitions ---

@router.get("/fields", response_model=list[CustomFieldRead])
async def list_fields(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(CustomFieldDefinition)
        .where(CustomFieldDefinition.system_id == system.id)
        .order_by(CustomFieldDefinition.order)
    )
    return result.scalars().all()


@router.post(
    "/fields",
    response_model=CustomFieldRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_scope("fields:write"))],
)
async def create_field(
    body: CustomFieldCreate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    field = CustomFieldDefinition(system_id=system.id, **body.model_dump())
    db.add(field)
    await db.commit()
    await db.refresh(field)
    return field


@router.get("/fields/{field_id}", response_model=CustomFieldRead)
async def get_field(
    field_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.system_id == system.id,
        )
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")
    return field


@router.patch(
    "/fields/{field_id}",
    response_model=CustomFieldRead,
    dependencies=[Depends(require_scope("fields:write"))],
)
async def update_field(
    field_id: uuid.UUID,
    body: CustomFieldUpdate,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)
    result = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.system_id == system.id,
        )
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(field, key, value)
    await db.commit()
    await db.refresh(field)
    return field


@router.delete(
    "/fields/{field_id}",
    dependencies=[Depends(require_scope("fields:delete"))],
)
async def delete_field(
    field_id: uuid.UUID,
    body: MemberDeleteConfirm | None = None,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    system = await _get_user_system(user, db)
    verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
    )
    result = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.system_id == system.id,
        )
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    if is_safeguarded(system, PendingActionType.FIELD_DELETE):
        pending = await queue_pending_action(
            db=db,
            system=system,
            user=user,
            action_type=PendingActionType.FIELD_DELETE,
            target_id=field.id,
            target_label=field.name,
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

    await db.delete(field)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Field values on members ---

@router.get("/members/{member_id}/fields", response_model=list[CustomFieldValueRead])
async def get_member_field_values(
    member_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # Verify member belongs to system
    member_result = await db.execute(
        select(Member).where(Member.id == member_id, Member.system_id == system.id)
    )
    if member_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    result = await db.execute(
        select(CustomFieldValue).where(CustomFieldValue.member_id == member_id)
    )
    return result.scalars().all()


@router.put(
    "/members/{member_id}/fields",
    response_model=list[CustomFieldValueRead],
    dependencies=[Depends(require_scope("fields:write"))],
)
async def set_member_field_values(
    member_id: uuid.UUID,
    body: list[CustomFieldValueSet],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    system = await _get_user_system(user, db)

    # Verify member belongs to system
    member_result = await db.execute(
        select(Member).where(Member.id == member_id, Member.system_id == system.id)
    )
    if member_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    # Validate all field IDs belong to this system
    field_ids = [item.field_id for item in body]
    field_result = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id.in_(field_ids),
            CustomFieldDefinition.system_id == system.id,
        )
    )
    valid_fields = {f.id for f in field_result.scalars().all()}
    if len(valid_fields) != len(field_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more field IDs are invalid",
        )

    # Upsert values
    for item in body:
        existing = await db.execute(
            select(CustomFieldValue).where(
                CustomFieldValue.field_id == item.field_id,
                CustomFieldValue.member_id == member_id,
            )
        )
        value = existing.scalar_one_or_none()
        if value is not None:
            value.value = item.value
        else:
            db.add(
                CustomFieldValue(
                    field_id=item.field_id,
                    member_id=member_id,
                    value=item.value,
                )
            )

    await db.commit()

    # Return all values for this member
    result = await db.execute(
        select(CustomFieldValue).where(CustomFieldValue.member_id == member_id)
    )
    return result.scalars().all()
