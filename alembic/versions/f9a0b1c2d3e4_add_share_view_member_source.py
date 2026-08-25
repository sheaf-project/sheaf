"""Record which group expansion added a member to a share view

Revision ID: f9a0b1c2d3e4
Revises: e8f9a0b1c2d3
Create Date: 2026-08-20

``share_view_members`` rows carried no provenance, so detaching a group from a
view had to guess which of its members it had put there - and it guessed by
re-reading the group's CURRENT roster. That over-removed in two ordinary
situations: a member the owner had also picked by hand, and a member an
overlapping group had brought in, both got pulled out of the view even though
the group being detached was not why they were there.

``added_via_group_id`` records the answer at the time the row is created, so
detach removes exactly the rows that expansion made. It is attribution only:
nothing reads it when projecting, and the snapshot semantic is unchanged -
group membership still never moves anyone into or out of a view, and a member
who has since left the group stays in the view until the owner says otherwise.

``ON DELETE SET NULL`` rather than CASCADE, deliberately. Deleting a group must
not delete anybody's view membership: that would be a group deletion silently
un-publishing (or, from the owner's side, silently rewriting) a curated view.
Nulling the stamp degrades those rows to treated-as-manual, which is the
conservative direction - they stay in the view until the owner removes them.

EXISTING ROWS ARE ALL LEFT NULL, and that is a decision rather than a
convenience: there is no record anywhere of which of them came from which
group, and the only way to invent one would be to re-read the group rosters -
the exact guess this column exists to stop making. NULL means treated-as-
manual, so a detach on a view built before this migration now removes NOTHING
rather than removing the group's current roster. That over-PRESERVES (the owner
sees the members still listed and can remove them, in a screen that shows every
member of the view) instead of over-REMOVING (which silently un-publishes
people the owner had chosen, with nothing on screen to say it happened). For a
feature whose failure mode is accidental exposure, the harm of a stale row the
owner can see and delete is much smaller than the harm of a silent removal, and
this way round the owner is never surprised by a deletion they did not ask for.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f9a0b1c2d3e4"
down_revision: str | None = "e8f9a0b1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "share_view_members",
        sa.Column(
            "added_via_group_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
            comment=(
                "Which group expansion created this row; NULL = added by hand "
                "(or the group has since been deleted, which deliberately "
                "degrades to treated-as-manual so deleting a group never "
                "silently detaches its members from views)."
            ),
        ),
    )
    op.create_foreign_key(
        "fk_share_view_members_added_via_group",
        "share_view_members",
        "groups",
        ["added_via_group_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_share_view_members_added_via_group_id",
        "share_view_members",
        ["added_via_group_id"],
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_index(
        "ix_share_view_members_added_via_group_id",
        table_name="share_view_members",
    )
    op.drop_constraint(
        "fk_share_view_members_added_via_group",
        "share_view_members",
        type_="foreignkey",
    )
    op.drop_column("share_view_members", "added_via_group_id")
