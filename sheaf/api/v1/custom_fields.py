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
    _validate_options_for_type,
    _validate_value_for_field,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.services.custom_fields import (
    decrypt_field_value,
    encrypt_field_value,
)
from sheaf.services.system_safety import (
    is_safeguarded,
    pending_finalize_after_by_target,
    queue_pending_action,
    verify_destructive_auth,
)


def _value_read(v: CustomFieldValue) -> CustomFieldValueRead:
    """Build CustomFieldValueRead with decrypted value."""
    return CustomFieldValueRead.model_validate({
        "field_id": v.field_id,
        "member_id": v.member_id,
        "value": decrypt_field_value(v.value),
    })

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
    fields = list(result.scalars().all())
    pending = await pending_finalize_after_by_target(
        db, system.id, PendingActionType.FIELD_DELETE
    )
    out: list[CustomFieldRead] = []
    for f in fields:
        fr = CustomFieldRead.model_validate(f)
        fr.pending_delete_at = pending.get(f.id)
        out.append(fr)
    return out


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
    pending = await pending_finalize_after_by_target(
        db, system.id, PendingActionType.FIELD_DELETE
    )
    fr = CustomFieldRead.model_validate(field)
    fr.pending_delete_at = pending.get(field.id)
    return fr


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
    if "options" in update_data:
        try:
            update_data["options"] = _validate_options_for_type(
                field.field_type, update_data["options"]
            )
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(e),
            ) from e
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
    return [_value_read(v) for v in result.scalars().all()]


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

    # Validate all field IDs belong to this system. Keep the field rows
    # around so we can run per-type value validation below — we need
    # field_type + options to know whether a submitted select value is
    # in the defined choices set.
    field_ids = [item.field_id for item in body]
    field_result = await db.execute(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.id.in_(field_ids),
            CustomFieldDefinition.system_id == system.id,
        )
    )
    field_by_id = {f.id: f for f in field_result.scalars().all()}
    if len(field_by_id) != len(field_ids):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One or more field IDs are invalid",
        )

    # Type-aware value validation. Only enforces the constraints that
    # don't need decryption — for select/multiselect with a `choices`
    # list, the submitted value must be one of them. Free-form
    # select/multiselect (choices unset, mobile's current shape) is
    # left alone.
    for item in body:
        defn = field_by_id[item.field_id]
        try:
            _validate_value_for_field(defn.field_type, defn.options, item.value)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Field '{defn.name}': {e}",
            ) from e

    # Upsert values. Stored value is the encrypted JSON-serialised plaintext.
    for item in body:
        existing = await db.execute(
            select(CustomFieldValue).where(
                CustomFieldValue.field_id == item.field_id,
                CustomFieldValue.member_id == member_id,
            )
        )
        value = existing.scalar_one_or_none()
        encrypted = encrypt_field_value(item.value)
        if value is not None:
            value.value = encrypted
        else:
            db.add(
                CustomFieldValue(
                    field_id=item.field_id,
                    member_id=member_id,
                    value=encrypted,
                )
            )

    await db.commit()

    # Return all values for this member
    result = await db.execute(
        select(CustomFieldValue).where(CustomFieldValue.member_id == member_id)
    )
    return [_value_read(v) for v in result.scalars().all()]
