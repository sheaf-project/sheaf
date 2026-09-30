"""Every member of every native Postgres enum in the models exists in the
migrated database.

Why this exists: `ActivityAction.NOTIFICATION_CHANNEL_DISABLED` was added to
the Python enum without the `ALTER TYPE ... ADD VALUE` migration a native
enum needs. Nothing noticed, because the only code path that wrote the value
was an error handler, and the failure was a commit error inside a
`gather(return_exceptions=True)`. It surfaced days later as an alert that
could not clear. The class of bug is latent by nature: an unmigrated value
fails only when that code path first runs in production.

This test makes it mechanical. The enum columns are read live from the ORM
metadata, so a new enum column or a new member is covered without anyone
remembering to list it here. The asymmetry is deliberate: a Python member
missing from the database is fatal, because the code will try to write it; a
database label missing from Python is harmless, because nothing writes it.

Runs against the test stack's database (the one the app under test migrated
at startup), through SHEAF_TEST_DB_URL, exactly as the conftest's admin
promotion helper does.
"""

from __future__ import annotations

import asyncio
import os

import pytest
import sqlalchemy as sa

from sheaf.models import Base


def _native_enum_columns() -> list[tuple[str, str, str, frozenset[str]]]:
    """(table, column, pg type name, python values) for every native enum."""
    found = []
    # Plain iteration, not `sorted_tables`: the dependency sort warns about
    # the users/invite_codes FK cycle, and order does not matter here.
    for table in Base.metadata.tables.values():
        for column in table.columns:
            if isinstance(column.type, sa.Enum) and column.type.native_enum:
                found.append(
                    (
                        table.name,
                        column.name,
                        column.type.name,
                        frozenset(column.type.enums),
                    )
                )
    return found


_ENUM_COLUMNS = _native_enum_columns()


def _database_labels() -> dict[str, frozenset[str]]:
    """pg type name -> its labels, for every enum type in the test database."""

    async def _run() -> dict[str, frozenset[str]]:
        from sqlalchemy.ext.asyncio import create_async_engine

        from sheaf.config import settings

        db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
        engine = create_async_engine(db_url)
        try:
            async with engine.connect() as conn:
                rows = await conn.execute(
                    sa.text(
                        "SELECT t.typname, e.enumlabel "
                        "FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid"
                    )
                )
                labels: dict[str, set[str]] = {}
                for typname, label in rows:
                    labels.setdefault(typname, set()).add(label)
        finally:
            await engine.dispose()
        return {k: frozenset(v) for k, v in labels.items()}

    return asyncio.run(_run())


@pytest.fixture(scope="module")
def database_labels() -> dict[str, frozenset[str]]:
    return _database_labels()


def test_the_inventory_is_not_empty():
    # If the ORM ever stopped declaring native enums this test would pass
    # vacuously; pin that it is checking something.
    assert len(_ENUM_COLUMNS) >= 5, _ENUM_COLUMNS


@pytest.mark.parametrize(
    "table, column, type_name, python_values",
    _ENUM_COLUMNS,
    ids=[f"{t}.{c}" for t, c, _, _ in _ENUM_COLUMNS],
)
def test_every_python_enum_member_exists_in_the_database(
    table, column, type_name, python_values, database_labels
):
    assert type_name in database_labels, (
        f"{table}.{column} is a native enum named {type_name!r} but no such "
        f"type exists in the migrated database"
    )
    missing = python_values - database_labels[type_name]
    assert not missing, (
        f"{table}.{column}: the code can write {sorted(missing)} but the "
        f"database enum {type_name!r} has never been told about them. Add an "
        f"ALTER TYPE {type_name} ADD VALUE migration (see "
        f"alembic/versions/t1u2v3w4x5y6 for the pattern)."
    )
