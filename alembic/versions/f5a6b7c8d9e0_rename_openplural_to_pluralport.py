"""Rename stored openplural values to pluralport

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-09-09

The OpenPlural data standard was renamed to PluralPort upstream (name
conflict). Data-only migration: re-label the two places the old format
name is stored as a value - the import-job source and the export-job
format. No DDL, so no lock_timeout guard is needed; both UPDATEs take
ordinary row locks on small tables.

Deliberately NOT touched: the systems.openplural_archive column. Its
name is baked into the AAD of every encrypted archive blob in
production, so renaming it would make existing data undecryptable. Only
stored format labels change here.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "UPDATE import_jobs SET source = 'pluralport_file' "
        "WHERE source = 'openplural_file'"
    )
    op.execute(
        "UPDATE export_jobs SET format = 'pluralport' "
        "WHERE format = 'openplural'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE import_jobs SET source = 'openplural_file' "
        "WHERE source = 'pluralport_file'"
    )
    op.execute(
        "UPDATE export_jobs SET format = 'openplural' "
        "WHERE format = 'pluralport'"
    )
