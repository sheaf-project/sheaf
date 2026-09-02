"""Guard the front state of custom fronts that were never curated into a view

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-08-27

A custom front ("Asleep", "Away", "Lost time") is an ordinary member as far as
the sharing model is concerned, so ``project_fronting`` will name one that is
in a view and public while it is fronting, and count it in the anonymous
``hidden_count`` even when it is outside the view. A public "Asleep" therefore
broadcasts sleep state on a URL anyone can poll. New custom fronts now land
with ``fronting_private`` on (see services/member_defaults); this backfills the
rows that already exist.

The ``share_view_members`` carve-out is the point of the statement. A custom
front the owner has actually added to a view was a deliberate act of curation:
they looked at that entity, picked it, and put it on a page. Flipping the guard
under them would silently change what their published page shows, and would do
it to the one group of people who demonstrably thought about it. Everything
else - every custom front sitting in the roster that no view has ever pointed
at - has never been curated either way, so the safe default is the one it gets.
Deliberately not scoped to *shared* views (a view with a live grant): a view
with no grant today can be published tomorrow, and the membership row is the
act of curation regardless of whether a link exists yet.

``NOT IN`` is safe against the usual footgun here because
``share_view_members.member_id`` is NOT NULL, so the subquery cannot yield a
NULL that would make the predicate match nothing.

Locks: a single UPDATE statement, so it is one transaction and one pass. It
takes ROW EXCLUSIVE on ``members`` (no conflict with ordinary reads/writes) and
row locks only on the rows it actually changes, which is a subset of one flag
on one member kind. The ``NOT fronting_private`` predicate keeps a re-run from
re-locking rows already done. ``lock_timeout`` is set anyway so a conflicting
session cannot park this behind a lock wait with the rest of the table's query
queue stacking up behind it.

Downgrade is a no-op, deliberately. ``fronting_private`` is a user setting and
this migration does not record what each row held before it ran, so there is no
prior value to restore - and guessing would mean un-guarding fronting state,
which is the direction that leaks. Clearing the flag stays a thing the owner
does per member through the gated release.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "e6f7a8b9c0d1"
down_revision: str | None = "d5e6f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.execute(
        """
        UPDATE members
        SET fronting_private = true
        WHERE is_custom_front
          AND NOT fronting_private
          AND id NOT IN (SELECT member_id FROM share_view_members)
        """
    )


def downgrade() -> None:
    # No-op: see the module docstring. The flag is a user setting whose prior
    # value this migration cannot know, and the only guess available is the
    # one that un-guards fronting state.
    pass
