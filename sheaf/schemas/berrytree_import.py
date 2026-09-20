"""Pydantic models for BerryTree data import."""

from pydantic import BaseModel, ConfigDict, Field

from sheaf.services.import_dedup import ImportConflictStrategy


class BTImportOptions(BaseModel):
    """What to import from the BerryTree export."""

    # Strict: a typo'd option key from a hand-rolled client 422s rather
    # than being silently ignored.
    model_config = ConfigDict(extra="forbid")

    conflict_strategy: ImportConflictStrategy = ImportConflictStrategy.SKIP
    system_profile: bool = True
    member_ids: list[str] | None = Field(
        None, max_length=10_000, description="BerryTree member IDs to import. None = all."
    )
    custom_fronts: bool = True
    custom_fields: bool = True
    tags: bool = True
    # BerryTree folders are member groupings with a parent chain, so they
    # land as Sheaf groups.
    folders: bool = True
    front_history: bool = True
    # Template members are scaffolding for making new members, not people.
    # They count against the member cap like anything else, so they stay off
    # by default and the preview says how many the file holds.
    templates: bool = False


class BTPreviewMember(BaseModel):
    id: str
    name: str


class BTPreviewCustomFront(BaseModel):
    id: str
    name: str


class BTUnsupportedSection(BaseModel):
    """One BerryTree section this importer cannot read yet, and how many
    records the file has in it.

    Surfaced per-section rather than as one lump so a user can see whether
    the thing they care about is the empty one or the full one.
    """

    name: str
    count: int


class BTPreviewSummary(BaseModel):
    system_name: str | None = None
    # The export's own `schema_version`. Null when the file omits it, which
    # is itself a sign the file did not come out of BerryTree's exporter.
    schema_version: int | None = None
    member_count: int = 0
    members: list[BTPreviewMember] = []
    # Members flagged `is_template`. Counted separately because they are
    # excluded unless the matching option is turned on.
    template_count: int = 0
    custom_front_count: int = 0
    custom_fronts: list[BTPreviewCustomFront] = []
    # BerryTree's fronting types ("Co-conscious", "Blurry"). Not members;
    # they annotate a front, so they ride along on the front's status text.
    fronting_type_count: int = 0
    front_history_count: int = 0
    folder_count: int = 0
    tag_count: int = 0
    custom_field_count: int = 0
    # Sections present in the file that this importer cannot map yet.
    unsupported_sections: list[BTUnsupportedSection] = []
    # BerryTree's exporter records its own failures in `_partial_errors`.
    # A file carrying these is missing data before it ever reaches us, so
    # the preview shows them verbatim. Deliberately preview-only: the job
    # event log is stored in plaintext and these strings may quote member
    # content, so the runner logs the count alone.
    export_errors: list[str] = []
    limit_warnings: list[str] = []


class BTImportResult(BaseModel):
    members_imported: int = 0
    custom_fronts_imported: int = 0
    # Dedup dispositions, covering all roster rows (members + custom
    # fronts) that matched an existing row instead of being created.
    members_skipped: int = 0
    members_updated: int = 0
    # Members whose privacy the file raised to public but the import kept as
    # it was, because publishing an already-shared member needs step-up
    # re-auth an import job cannot ask for.
    members_privacy_skipped: int = 0
    templates_skipped: int = 0
    fronts_imported: int = 0
    fronts_skipped: int = 0
    groups_imported: int = 0
    groups_skipped: int = 0
    tags_imported: int = 0
    tags_skipped: int = 0
    custom_fields_imported: int = 0
    custom_fields_skipped: int = 0
    warnings: list[str] = []
