"""Add newsletter opt-in and email deliverability columns to users

Revision ID: h8i9j0k1l2m3
Revises: g7h8i9j0k1l2
Create Date: 2026-04-11
"""

from alembic import op
import sqlalchemy as sa

revision = "h8i9j0k1l2m3"
down_revision = "g7h8i9j0k1l2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "newsletter_opt_in",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "newsletter_opted_in_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    email_delivery_status = sa.Enum(
        "ok",
        "soft_bouncing",
        "hard_bounced",
        "complained",
        name="emaildeliverystatus",
    )
    email_delivery_status.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "email_delivery_status",
            email_delivery_status,
            nullable=False,
            server_default="ok",
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_delivery_status_changed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_soft_bounce_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "email_revalidation_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "email_revalidation_required")
    op.drop_column("users", "email_soft_bounce_count")
    op.drop_column("users", "email_delivery_status_changed_at")
    op.drop_column("users", "email_delivery_status")
    sa.Enum(name="emaildeliverystatus").drop(op.get_bind(), checkfirst=True)
    op.drop_column("users", "newsletter_opted_in_at")
    op.drop_column("users", "newsletter_opt_in")
