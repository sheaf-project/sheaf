"""Share view flag: include every public member, live

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-20

Two additive boolean columns on ``share_views``:

- ``include_all_public_members`` - when set, the view's roster is every member
  whose privacy ceiling is ``public``, evaluated at read time, instead of only
  the members listed in ``share_view_members``. Defaults FALSE: every view that
  exists today keeps serving exactly the roster it serves now, and a new
  capability never arrives switched on.
- ``pending_include_all_public_members`` - the staging twin every exposure flag
  carries. Turning the flag on while the view is already shared is a loosening,
  so the new value parks here and the sharing finalizer copies it across once
  ``flags_activate_at`` passes. Nullable, because NULL means "nothing staged".

Both are plain ``ADD COLUMN`` with a constant default, so Postgres records the
default in the catalogue rather than rewriting the table - but the statement
still takes ACCESS EXCLUSIVE on ``share_views`` for as long as it waits for
that lock, queueing every other query on the table behind it. ``lock_timeout``
makes it fail fast instead; re-run in a quiet window.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.add_column(
        "share_views",
        sa.Column(
            "include_all_public_members",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "share_views",
        sa.Column(
            "pending_include_all_public_members", sa.Boolean(), nullable=True
        ),
    )


def downgrade() -> None:
    op.execute("SET lock_timeout = '3s'")

    op.drop_column("share_views", "pending_include_all_public_members")
    op.drop_column("share_views", "include_all_public_members")
