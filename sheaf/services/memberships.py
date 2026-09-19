"""Bulk membership lookups for the group and tag expansions.

A client that wants to know which members are in which group, or carry which
tag, used to need one request per group and one per tag: `1 + N + M` round
trips to build a map that is small and changes rarely. That is the wrong shape
for a watch fetching it over a Bluetooth link, and it is paid again by the
phone, its widgets, and the web client.

Every function here answers the whole question in ONE query over the
association table. That is the point of the module: doing it per group inside
a loop would move the N+1 from the client to the server, where it is harder to
see and nobody is counting round trips any more.

Ordering is by member id, and deliberately not by name. Member names are
encrypted at rest (`Member.name` carries `info={"encrypted": True}`), so there
is no `ORDER BY` that could sort by them; the only way would be to load and
decrypt every member on a call whose entire purpose is to be cheap. A caller
rendering these ids has the member records already, because a uuid is not
something you can display, so it can sort by the names it is holding. What the
server owes it is a stable order, which this is.

Scope note: none of this is new permission surface. Everything returned is
already reachable by the same caller at the same scope, one request per group
away, through `/v1/groups/{id}/members` and `/v1/tags/{id}/members`. The
members-side expansions are the exception and are gated at the endpoint, since
those cross into a resource the caller may not hold a scope for.
"""

from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sheaf.models.group import Group
from sheaf.models.member import group_members, member_tags
from sheaf.models.tag import Tag


async def member_ids_by_group(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """group id -> member ids, for every group in the system.

    Groups with no members are absent rather than mapped to `[]`; the caller
    fills those in, because it is the one that knows which groups it asked
    about.
    """
    result = await db.execute(
        select(group_members.c.group_id, group_members.c.member_id)
        .join(Group, Group.id == group_members.c.group_id)
        .where(Group.system_id == system_id)
        .order_by(group_members.c.group_id, group_members.c.member_id)
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for group_id, member_id in result.all():
        out[group_id].append(member_id)
    return dict(out)


async def member_ids_by_tag(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """tag id -> member ids, for every tag in the system."""
    result = await db.execute(
        select(member_tags.c.tag_id, member_tags.c.member_id)
        .join(Tag, Tag.id == member_tags.c.tag_id)
        .where(Tag.system_id == system_id)
        .order_by(member_tags.c.tag_id, member_tags.c.member_id)
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for tag_id, member_id in result.all():
        out[tag_id].append(member_id)
    return dict(out)


async def group_ids_by_member(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """member id -> group ids. The same rows as `member_ids_by_group`, keyed
    the way a client that starts from a member wants them."""
    result = await db.execute(
        select(group_members.c.member_id, group_members.c.group_id)
        .join(Group, Group.id == group_members.c.group_id)
        .where(Group.system_id == system_id)
        .order_by(group_members.c.member_id, group_members.c.group_id)
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for member_id, group_id in result.all():
        out[member_id].append(group_id)
    return dict(out)


async def tag_ids_by_member(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, list[uuid.UUID]]:
    """member id -> tag ids."""
    result = await db.execute(
        select(member_tags.c.member_id, member_tags.c.tag_id)
        .join(Tag, Tag.id == member_tags.c.tag_id)
        .where(Tag.system_id == system_id)
        .order_by(member_tags.c.member_id, member_tags.c.tag_id)
    )
    out: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for member_id, tag_id in result.all():
        out[member_id].append(tag_id)
    return dict(out)


async def member_counts_by_group(
    db: AsyncSession, system_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """group id -> how many members it holds.

    Served unconditionally on `GroupRead`, unlike the id list: it is one
    aggregate, it needs no decryption, and a list screen wanting to say "12
    members" otherwise fetches twelve members and throws them away.

    Counts every member of the group, archived and pending-delete included,
    for the same reason the id list does: they are still in the group, and a
    count that quietly disagreed with the roster below it would read as a bug.
    """
    result = await db.execute(
        select(group_members.c.group_id, func.count(group_members.c.member_id))
        .join(Group, Group.id == group_members.c.group_id)
        .where(Group.system_id == system_id)
        .group_by(group_members.c.group_id)
    )
    return {group_id: count for group_id, count in result.all()}


async def member_ids_for_group(
    db: AsyncSession, group_id: uuid.UUID
) -> list[uuid.UUID]:
    """One group's member ids, for the detail endpoint.

    The caller has already established that this group belongs to them, so
    this takes the group id rather than re-deriving ownership.
    """
    result = await db.execute(
        select(group_members.c.member_id)
        .where(group_members.c.group_id == group_id)
        .order_by(group_members.c.member_id)
    )
    return list(result.scalars().all())


async def member_ids_for_tag(
    db: AsyncSession, tag_id: uuid.UUID
) -> list[uuid.UUID]:
    """One tag's member ids, for the detail endpoint."""
    result = await db.execute(
        select(member_tags.c.member_id)
        .where(member_tags.c.tag_id == tag_id)
        .order_by(member_tags.c.member_id)
    )
    return list(result.scalars().all())


__all__ = [
    "group_ids_by_member",
    "member_counts_by_group",
    "member_ids_by_group",
    "member_ids_by_tag",
    "member_ids_for_group",
    "member_ids_for_tag",
    "tag_ids_by_member",
]
