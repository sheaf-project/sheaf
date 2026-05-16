"""Pydantic models for Sheaf native re-import.

Sheaf's own export format round-trips through this importer. The
legacy `/v1/import/sheaf` endpoint took these as query params; the
async runner carries them as an options dict in the ImportJob's
payload_metadata, validated against this model.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SheafImportOptions(BaseModel):
    """What to import from a Sheaf export. Each flag gates one section;
    member_ids optionally narrows the member set (and everything that
    references it) to a deselected subset chosen on the preview screen.
    """

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    system_profile: bool = True
    member_ids: list[str] | None = Field(default=None, max_length=10_000)
    fronts: bool = True
    groups: bool = True
    tags: bool = True
    custom_fields: bool = True
