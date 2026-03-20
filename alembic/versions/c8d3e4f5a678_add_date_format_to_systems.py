"""Add date_format to systems

Revision ID: c8d3e4f5a678
Revises: b7e1f2c3d456
Create Date: 2026-03-19 01:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c8d3e4f5a678"
down_revision: Union[str, None] = "b7e1f2c3d456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE dateformat AS ENUM ('dmy', 'mdy', 'ymd')")
    op.add_column(
        "systems",
        sa.Column(
            "date_format",
            sa.Enum("dmy", "mdy", "ymd", name="dateformat", create_type=False),
            nullable=False,
            server_default="ymd",
        ),
    )


def downgrade() -> None:
    op.drop_column("systems", "date_format")
    op.execute("DROP TYPE dateformat")
