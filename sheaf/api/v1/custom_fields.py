import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.auth.dependencies import get_current_user, require_scope
from sheaf.database import get_db
from sheaf.models.custom_field import CustomFieldDefinition, CustomFieldValue
from sheaf.models.member import Member
from sheaf.models.pending_action import PendingActionType
from sheaf.models.system import PrivacyLevel, System
from sheaf.models.user import User
from sheaf.observability.metrics import custom_fields_created_total
from sheaf.schemas.custom_field import (
    MAX_CUSTOM_FIELD_VALUE_CHARS,
    CustomFieldCreate,
    CustomFieldRead,
    CustomFieldUpdate,
    CustomFieldValueRead,
    CustomFieldValueSet,
    _validate_options_for_type,
    _validate_value_for_field,
    value_over_text_cap,
)
from sheaf.schemas.member import MemberDeleteConfirm
from sheaf.services.custom_fields import (
    decrypt_field_value,
    encrypt_field_value,
)
from sheaf.services.sharing import (
    field_privacy_raise_exposes,
    reject_mixed_exposure_directions,
    visibility_grace_days,
    visibility_step_up_required,
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
        "value": decrypt_field_value(v.value, v.id),
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
        db, system, PendingActionType.FIELD_DELETE
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
    fields = body.model_dump()
    # Step-up credentials are not field columns; drop them before the row is
    # built so they can never be persisted.
    password = fields.pop("password", None)
    totp_code = fields.pop("totp_code", None)

    # Creating a definition straight to `public` exposes exactly what raising
    # an existing one does, so it runs the SAME check and gets the same
    # treatment: step-up now, and the field is born private with the raise
    # staged behind the grace window. Without this, "delete it and add it back
    # public" would walk around the PATCH gate entirely.
    #
    # The check is asked without a field id, because there is no field yet and
    # so nothing selects it - see `field_privacy_raise_exposes`. In practice it
    # only bites when the system is already serving some other field through a
    # live view, which is the honest reading of "this account is publishing
    # custom fields right now".
    if (
        fields.get("privacy") == PrivacyLevel.PUBLIC
        and visibility_step_up_required(system)
        and await field_privacy_raise_exposes(db, system)
    ):
        await verify_destructive_auth(user, system, password, totp_code, db)
        grace = visibility_grace_days(system)
        if grace > 0:
            fields["privacy"] = PrivacyLevel.PRIVATE
            fields["pending_privacy"] = PrivacyLevel.PUBLIC
            fields["privacy_activates_at"] = datetime.now(UTC) + timedelta(
                days=grace
            )

    field = CustomFieldDefinition(system_id=system.id, **fields)
    db.add(field)
    await db.commit()
    custom_fields_created_total.inc()
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
        db, system, PendingActionType.FIELD_DELETE
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
    """Edit a field definition, including moving it up or down the privacy ladder.

    Raising a definition to `public` EXPOSES it, but only when something would
    actually serve it - see `field_privacy_raise_exposes`. When it would, this
    behaves exactly like raising a member, an edge or a group: re-auth now, and
    the raise itself waits out the grace window as `pending_privacy` +
    `privacy_activates_at` while the live level stays where it was.

    Every other direction is instant and ungated. Lowering is the un-exposing
    direction and nothing may slow it down, and ANY such change lands on top of
    whatever was staged rather than queueing behind it - setting private, or
    friends, while a public raise is pending cancels that raise outright. The
    last thing the owner asked for wins, and it wins at its own gate. private ->
    friends is ungated for the same reason it is on members, edges and groups:
    the friends tier is parked and every grant that exists today is public-tier,
    so it exposes nobody. When friends lands, this check and
    `share_projection._exposed_fields` have to become audience-aware together.

    The level applies to this field on every member. There is deliberately no
    per-member-per-field setting to reconcile here.

    House rule shared with every other exposure PATCH: one body may not carry
    both a raise and a lowering, because the raise's step-up would then be able
    to fail the lowering with it. `privacy` is this endpoint's only exposure
    axis, so the check cannot bite here yet - it is applied so a second axis
    added later cannot land without it.
    """
    system = await _get_user_system(user, db)
    # FOR UPDATE for the same reason `update_member` and the group and edge
    # PATCHes take it: two concurrent privacy writes on one definition must not
    # interleave into a state where the live level and the staged level
    # disagree about which way the owner was going.
    result = await db.execute(
        select(CustomFieldDefinition)
        .where(
            CustomFieldDefinition.id == field_id,
            CustomFieldDefinition.system_id == system.id,
        )
        .with_for_update()
    )
    field = result.scalar_one_or_none()
    if field is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Field not found")

    update_data = body.model_dump(exclude_unset=True)
    # Step-up credentials are not field columns; drop them before anything
    # iterates the update so they can never be persisted.
    password = update_data.pop("password", None)
    totp_code = update_data.pop("totp_code", None)

    requested_privacy = update_data.pop("privacy", None)
    exposes = False
    if (
        requested_privacy == PrivacyLevel.PUBLIC
        and field.privacy != PrivacyLevel.PUBLIC
        and visibility_step_up_required(system)
    ):
        exposes = await field_privacy_raise_exposes(db, system, field.id)

    # Same uniform check as the other exposure PATCHes: one body may not both
    # raise and lower exposure, because the raise's step-up would then be able
    # to fail the lowering with it. `privacy` is this endpoint's only exposure
    # axis, so it cannot fire today; it is here so a second axis added later
    # inherits the rule rather than rediscovering the bug.
    reject_mixed_exposure_directions(raises=exposes, lowers=False)

    if exposes:
        await verify_destructive_auth(user, system, password, totp_code, db)
        grace = visibility_grace_days(system)
        if grace > 0:
            field.pending_privacy = PrivacyLevel.PUBLIC
            field.privacy_activates_at = datetime.now(UTC) + timedelta(days=grace)
        else:
            field.privacy = PrivacyLevel.PUBLIC
            field.pending_privacy = None
            field.privacy_activates_at = None
    elif requested_privacy is not None:
        field.privacy = requested_privacy
        field.pending_privacy = None
        field.privacy_activates_at = None

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
    await verify_destructive_auth(
        user,
        system,
        body.password if body else None,
        body.totp_code if body else None,
        db,
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

    # Length is checked HERE rather than in the schema validator because it is
    # the only rule on this endpoint that depends on what is already stored.
    # The cap bounds new text; it does not retroactively invalidate what was
    # stored before it existed. Nothing rewrites an over-cap row, imports carry
    # one in at full length, and a value that comes back byte-identical to the
    # stored one is accepted - otherwise somebody holding a long value from
    # before the cap could not save a change to any OTHER field on that member
    # without first destroying that one. Change the long value and the cap
    # applies again, because that is new text.
    stored_by_field: dict[uuid.UUID, CustomFieldValue] | None = None
    for item in body:
        if not value_over_text_cap(item.value):
            continue
        if stored_by_field is None:
            stored_result = await db.execute(
                select(CustomFieldValue).where(
                    CustomFieldValue.member_id == member_id
                )
            )
            stored_by_field = {
                v.field_id: v for v in stored_result.scalars().all()
            }
        stored = stored_by_field.get(item.field_id)
        unchanged = (
            stored is not None
            and decrypt_field_value(stored.value, stored.id) == item.value
        )
        if not unchanged:
            defn = field_by_id[item.field_id]
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Field '{defn.name}': text values are limited to "
                    f"{MAX_CUSTOM_FIELD_VALUE_CHARS:,} characters."
                ),
            )

    # Upsert values. Stored value is the encrypted JSON-serialised plaintext.
    for item in body:
        existing = await db.execute(
            select(CustomFieldValue).where(
                CustomFieldValue.field_id == item.field_id,
                CustomFieldValue.member_id == member_id,
            )
        )
        value = existing.scalar_one_or_none()
        if value is not None:
            value.value = encrypt_field_value(item.value, value.id)
        else:
            vid = uuid.uuid4()
            db.add(
                CustomFieldValue(
                    id=vid,
                    field_id=item.field_id,
                    member_id=member_id,
                    value=encrypt_field_value(item.value, vid),
                )
            )

    await db.commit()

    # Return all values for this member
    result = await db.execute(
        select(CustomFieldValue).where(CustomFieldValue.member_id == member_id)
    )
    return [_value_read(v) for v in result.scalars().all()]
