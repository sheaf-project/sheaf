"""Add delete_confirmation to systems

Revision ID: b7e1f2c3d456
Revises: a83c4a7a3905
Create Date: 2026-03-18 20:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b7e1f2c3d456"
down_revision: Union[str, None] = "a83c4a7a3905"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE deleteconfirmation AS ENUM ('none', 'password', 'totp', 'both')")
    op.add_column(
        "systems",
        sa.Column(
            "delete_confirmation",
            sa.Enum("none", "password", "totp", "both", name="deleteconfirmation", create_type=False),
            nullable=False,
            server_default="none",
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "delete_confirmation")
    op.execute("DROP TYPE deleteconfirmation")
