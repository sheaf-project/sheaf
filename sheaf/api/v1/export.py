from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sheaf.auth.dependencies import get_current_user
from sheaf.database import get_db
from sheaf.models.custom_field import CustomFieldDefinition
from sheaf.models.front import Front
from sheaf.models.group import Group
from sheaf.models.member import Member
from sheaf.models.system import System
from sheaf.models.tag import Tag
from sheaf.models.user import User
from sheaf.services.custom_fields import field_value_plaintext
from sheaf.services.members import member_plaintext

router = APIRouter(prefix="/export", tags=["export"])


@router.get("")
async def export_all(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Export all user data as JSON. Critical for data portability and
    pre-pruning exports in aaS free tier."""

    # System
    result = await db.execute(select(System).where(System.user_id == user.id))
    system = result.scalar_one_or_none()
    if system is None:
        return {"system": None, "members": [], "fronts": [], "groups": [], "tags": [], "fields": []}

    # Members. Member.name is encrypted ciphertext, so DB-side ORDER BY on
    # it is meaningless — sort in Python after decrypting names below.
    members_result = await db.execute(
        select(Member).where(Member.system_id == system.id)
    )
    members_with_plaintext = [
        (m, *member_plaintext(m)) for m in members_result.scalars().all()
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

    return {
        "version": "1",
        "system": {
            "id": str(system.id),
            "name": system.name,
            "description": system.description,
            "tag": system.tag,
            "avatar_url": system.avatar_url,
            "color": system.color,
            "privacy": system.privacy.value,
        },
        "members": [
            {
                "id": str(m.id),
                "name": name,
                "display_name": m.display_name,
                "description": description,
                "pronouns": m.pronouns,
                "avatar_url": m.avatar_url,
                "color": m.color,
                "birthday": m.birthday,
                "privacy": m.privacy.value,
                "created_at": m.created_at.isoformat(),
            }
            for (m, name, description) in members_with_plaintext
        ],
        "fronts": [
            {
                "id": str(f.id),
                "started_at": f.started_at.isoformat(),
                "ended_at": f.ended_at.isoformat() if f.ended_at else None,
                "member_ids": [str(m.id) for m in f.members],
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
                        "value": field_value_plaintext(v),
                    }
                    for v in fd.values
                ],
            }
            for fd in fields
        ],
    }
