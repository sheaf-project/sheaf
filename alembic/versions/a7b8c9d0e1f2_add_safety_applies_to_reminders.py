"""Add systems.safety_applies_to_reminders column

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-05-07

Reminders had been bundled under the existing
`safety_applies_to_notifications` toggle, which also covered watch
tokens and channels. That made the safety category copy misleading
("Deleting a channel or revoking a watcher" — but reminders too?).
Splitting reminders into its own toggle restores the 1-toggle-per-
destructive-domain shape and makes the UI explicit.
"""

import sqlalchemy as sa

from alembic import op

revision = "a7b8c9d0e1f2"
down_revision = "z6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "systems",
        sa.Column(
            "safety_applies_to_reminders",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "safety_applies_to_reminders")
