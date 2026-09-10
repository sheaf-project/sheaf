"""PLURALPORT_MAX_PRESERVED_MB env alias.

The setting was OPENPLURAL_MAX_PRESERVED_MB before the format's upstream
rename to PluralPort. Deploys that set the old name must keep working, so
the field carries an AliasChoices validation alias; these tests pin that
both spellings resolve (and that the new one wins when both are set).

`_env_file=None` disables reading the repo `.env` so the tests exercise
env vars and defaults only, matching the other config tests.
"""

from __future__ import annotations

from sheaf.config import Settings


def test_default_when_neither_env_var_set(monkeypatch):
    monkeypatch.delenv("PLURALPORT_MAX_PRESERVED_MB", raising=False)
    monkeypatch.delenv("OPENPLURAL_MAX_PRESERVED_MB", raising=False)

    assert Settings(_env_file=None).pluralport_max_preserved_mb == 8


def test_legacy_env_var_still_honoured(monkeypatch):
    monkeypatch.delenv("PLURALPORT_MAX_PRESERVED_MB", raising=False)
    monkeypatch.setenv("OPENPLURAL_MAX_PRESERVED_MB", "3")

    assert Settings(_env_file=None).pluralport_max_preserved_mb == 3


def test_new_env_var_wins_over_legacy(monkeypatch):
    monkeypatch.setenv("PLURALPORT_MAX_PRESERVED_MB", "5")
    monkeypatch.setenv("OPENPLURAL_MAX_PRESERVED_MB", "3")

    assert Settings(_env_file=None).pluralport_max_preserved_mb == 5
