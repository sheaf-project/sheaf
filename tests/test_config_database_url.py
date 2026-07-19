"""DATABASE_URL derivation from POSTGRES_* parts.

`_env_file=None` disables reading the repo `.env` so these exercise the
derivation logic against env vars and defaults only.
"""

from __future__ import annotations

from sheaf.config import Settings


def test_database_url_derived_from_postgres_password(monkeypatch):
    """Unset DATABASE_URL is built from POSTGRES_PASSWORD, URL-encoded."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_PASSWORD", "p@ss:w/rd")

    s = Settings(_env_file=None)

    assert (
        s.database_url
        == "postgresql+asyncpg://sheaf:p%40ss%3Aw%2Frd@db:5432/sheaf"
    )


def test_explicit_database_url_is_used_verbatim(monkeypatch):
    """An explicit DATABASE_URL wins; the POSTGRES_* parts are ignored."""
    monkeypatch.setenv(
        "DATABASE_URL", "postgresql+asyncpg://u:p@ext.example:5433/other"
    )
    monkeypatch.setenv("POSTGRES_PASSWORD", "ignored")

    s = Settings(_env_file=None)

    assert s.database_url == "postgresql+asyncpg://u:p@ext.example:5433/other"


def test_database_url_honours_all_postgres_parts(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("POSTGRES_USER", "app")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_HOST", "pg.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6543")
    monkeypatch.setenv("POSTGRES_DB", "sheafdb")

    s = Settings(_env_file=None)

    assert s.database_url == "postgresql+asyncpg://app:secret@pg.internal:6543/sheafdb"


def test_default_password_still_trips_insecure_guard(monkeypatch):
    """A bare deploy derives the default password, which the derived URL
    surfaces so the insecure-default check keeps firing."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("POSTGRES_PASSWORD", raising=False)

    s = Settings(_env_file=None)

    assert "changeme" in s.database_url
