"""Drift guard between the DB schema and the encrypted-cell registry.

Pure-unit: introspects `sheaf.encrypted_fields` and `Base.metadata` only,
so it needs neither a database nor the encryption key. It fails loudly when
the two inventories drift apart, which is the failure mode that silently
leaves a column on the legacy (unbound) ciphertext format forever.

Two sides are compared:

- The *registry*: every public helper in `sheaf.encrypted_fields`, each of
  which returns the AAD `sheaf-fe-v2|{table}|{column}|{pk}` for one cell.
- The *schema*: every column whose `info` carries an `encrypted` marker
  (`True`, or `"json"` for a ciphertext embedded inside a JSONB column).

A logical JSON path like `payload_metadata.encrypted_credential` maps to the
physical column `payload_metadata`, which must itself be marked
`info={"encrypted": "json"}`.
"""

import inspect

from sqlalchemy.dialects.postgresql import UUID

from sheaf import encrypted_fields
from sheaf.models import Base

# A distinctive pk value we can look for in the last AAD field. Deliberately
# not a real UUID and not containing '|' or '.', so a helper that hand-rolls
# the AAD string instead of going through field_aad still round-trips it here.
_SENTINEL_PK = "SENTINEL-PK-0000"

_DESIGN_DOC = "sheaf-design-docs/field-encryption-context-binding.md"


def _registry_helpers():
    """Public AAD helpers defined in sheaf.encrypted_fields (not imports)."""
    return [
        fn
        for name, fn in inspect.getmembers(encrypted_fields, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == encrypted_fields.__name__
    ]


def _registry_bindings():
    """Map each helper to the (table, logical_column) it binds.

    Also asserts the AAD *shape* per helper, so a typo'd hand-rolled f-string
    that bypasses field_aad is caught here rather than silently mis-binding.
    """
    bindings = []
    for fn in _registry_helpers():
        raw = fn(_SENTINEL_PK)
        assert isinstance(raw, bytes), (
            f"{fn.__name__} returned {type(raw).__name__}, expected bytes; "
            f"AAD helpers must return the field_aad() byte string."
        )
        parts = raw.decode().split("|")
        assert len(parts) == 4, (
            f"{fn.__name__} produced AAD {raw!r} with {len(parts)} '|'-separated "
            f"parts, expected exactly 4 (sheaf-fe-v2|table|column|pk). Use "
            f"field_aad(table, column, pk) instead of a hand-rolled string."
        )
        label, table, column, pk = parts
        assert label == "sheaf-fe-v2", (
            f"{fn.__name__} produced AAD label {label!r}, expected 'sheaf-fe-v2'."
        )
        assert pk == _SENTINEL_PK, (
            f"{fn.__name__} put {pk!r} in the pk position, expected the passed "
            f"pk {_SENTINEL_PK!r}; the helper must pass its argument through as "
            f"the row pk, not substitute a constant."
        )
        bindings.append((fn.__name__, table, column))
    return bindings


def _schema_marked():
    """Every (table, column, marker) carrying an `encrypted` info marker."""
    marked = []
    for table in Base.metadata.tables.values():
        for column in table.columns:
            marker = column.info.get("encrypted")
            if marker:
                marked.append((table.name, column.name, marker))
    return marked


def _resolve(table, logical):
    """Resolve a registry (table, logical_column) to a physical column.

    Returns (physical_column, None) on success or (None, reason) on failure.
    Rule: exact column match AND the column is marked exactly
    info={"encrypted": True} (a "json"-marked column holds ciphertext at a
    JSON *path*, so an exact-column helper on it is a mis-binding), OR the
    logical name starts with `physical_column + "."` and that physical
    column is marked info={"encrypted": "json"}. Requiring the strict marker
    on the exact-match branch keeps the check two-way: a helper pointing at
    a real but PLAINTEXT (or merely truthy-marked) column must fail, not
    pass.
    """
    t = Base.metadata.tables.get(table)
    if t is None:
        return None, f"table {table!r} does not exist in Base.metadata"
    if logical in t.columns:
        if t.columns[logical].info.get("encrypted") is not True:
            return None, (
                f"column {table}.{logical} exists but is not marked exactly "
                f'info={{"encrypted": True}} - either the helper binds a '
                f"plaintext or json-marked column, or the marker is missing "
                f"in sheaf/models/"
            )
        return logical, None
    if "." in logical:
        physical = logical.split(".", 1)[0]
        col = t.columns.get(physical)
        if col is None:
            return None, f"physical column {table}.{physical!r} does not exist"
        if col.info.get("encrypted") != "json":
            return None, (
                f"column {table}.{physical} is not marked "
                f'info={{"encrypted": "json"}}, so the JSON path {logical!r} '
                f"cannot resolve to it"
            )
        return physical, None
    return None, f"column {table}.{logical!r} does not exist"


def test_registry_bindings_resolve_to_real_columns():
    """Every registry helper binds a real (table, column) in the schema."""
    for fn_name, table, column in _registry_bindings():
        physical, reason = _resolve(table, column)
        assert physical is not None, (
            f"encrypted_fields.{fn_name} binds {table}.{column}, but {reason}. "
            f"Fix the helper's table/column, or mark the column in sheaf/models/."
        )


def test_every_marked_column_has_a_registry_helper():
    """Every schema-marked encrypted column is covered by a helper."""
    bindings = _registry_bindings()
    for table, column, marker in _schema_marked():
        if marker == "json":
            covered = any(
                b_table == table and b_col.startswith(column + ".")
                for _, b_table, b_col in bindings
            )
        else:
            covered = any(
                b_table == table and b_col == column
                for _, b_table, b_col in bindings
            )
        assert covered, (
            f"{table}.{column} is marked encrypted in sheaf/models/ but no helper "
            f"in sheaf/encrypted_fields.py binds it. Add a helper and wire the cell "
            f"into the re-encrypt sweep, or the column stays on the legacy format."
        )


def test_no_duplicate_registry_bindings():
    """No two helpers bind the same (table, logical column)."""
    seen = {}
    for fn_name, table, column in _registry_bindings():
        key = (table, column)
        assert key not in seen, (
            f"encrypted_fields.{fn_name} and encrypted_fields.{seen[key]} both bind "
            f"{table}.{column}; each cell must have exactly one helper."
        )
        seen[key] = fn_name


# The golden binding map: every AAD helper and the exact (table, logical
# column) its body must bind. Deliberately a full literal, not derived from
# the module under test: the set-based checks above cannot see a helper whose
# BODY binds the wrong cell while the overall cell set stays intact (e.g.
# member_name_aad returning the members.note AAD while member_note_aad
# returns members.name). Changing any binding must be a conscious edit here,
# because the (table, column) strings are baked into the AAD of stored rows.
_GOLDEN_BINDINGS = {
    "user_email_aad": ("users", "email"),
    "user_totp_secret_aad": ("users", "totp_secret"),
    "user_recovery_codes_aad": ("users", "recovery_codes"),
    "member_name_aad": ("members", "name"),
    "member_description_aad": ("members", "description"),
    "member_note_aad": ("members", "note"),
    "system_note_aad": ("systems", "note"),
    "system_openplural_archive_aad": ("systems", "openplural_archive"),
    "front_custom_status_aad": ("fronts", "custom_status"),
    "journal_title_aad": ("journal_entries", "title"),
    "journal_body_aad": ("journal_entries", "body"),
    "revision_title_aad": ("content_revisions", "title"),
    "revision_body_aad": ("content_revisions", "body"),
    "reminder_title_aad": ("reminders", "title"),
    "reminder_body_aad": ("reminders", "body"),
    "message_body_aad": ("messages", "body"),
    "poll_question_aad": ("polls", "question"),
    "poll_description_aad": ("polls", "description"),
    "poll_option_text_aad": ("poll_options", "text"),
    "custom_field_value_aad": ("custom_field_values", "value"),
    "webhook_secret_aad": ("notification_channels", "webhook_secret_encrypted"),
    "import_credential_aad": (
        "import_jobs", "payload_metadata.encrypted_credential",
    ),
    "pending_target_label_aad": ("pending_actions", "target_label"),
    "pending_fronting_names_aad": ("pending_actions", "fronting_member_names"),
}


def test_registry_matches_golden_binding_map():
    """Every helper binds exactly the golden (table, logical column) - both
    directions: no helper missing from the map, no map entry without a
    helper, and no helper whose body produces a different cell's AAD."""
    actual = {
        fn_name: (table, column)
        for fn_name, table, column in _registry_bindings()
    }
    assert actual == _GOLDEN_BINDINGS, (
        f"helpers not in golden map: {sorted(actual.keys() - _GOLDEN_BINDINGS.keys())}; "
        f"golden entries without a helper: {sorted(_GOLDEN_BINDINGS.keys() - actual.keys())}; "
        f"mis-bound: "
        f"{ {k: (actual[k], _GOLDEN_BINDINGS[k]) for k in actual.keys() & _GOLDEN_BINDINGS.keys() if actual[k] != _GOLDEN_BINDINGS[k]} }. "
        f"A binding change rewrites the AAD baked into stored rows - only "
        f"edit the golden map as a conscious, reviewed decision."
    )


def test_registry_and_schema_sets_are_identical():
    """The resolved registry cells and the marked schema columns are the
    same set - a direct two-way comparison on top of the per-item checks,
    so nothing can slip through an asymmetry between them."""
    resolved = set()
    for _fn_name, table, column in _registry_bindings():
        physical, reason = _resolve(table, column)
        assert physical is not None, reason
        resolved.add((table, physical))
    marked = {(table, column) for table, column, _marker in _schema_marked()}
    assert resolved == marked, (
        f"registry-only: {sorted(resolved - marked)}; "
        f"schema-only: {sorted(marked - resolved)}"
    )


# Pinned exact expected contents of CIPHERTEXT_COPIES. Kept literal so that
# deleting an entry (which would silently drop it from the Phase 2 sweep and
# strand its ciphertext copies on v1) fails this test rather than passing
# vacuously. Adding a new stored-ciphertext-copy site is a conscious edit here.
_EXPECTED_CIPHERTEXT_COPIES = {
    (
        "front_audit_events",
        "before_snapshot.custom_status_encrypted",
        "fronts.custom_status",
    ),
    (
        "front_audit_events",
        "after_snapshot.custom_status_encrypted",
        "fronts.custom_status",
    ),
}


def test_ciphertext_copies_match_pinned_set():
    """CIPHERTEXT_COPIES is exactly the pinned set - guards against a silent
    deletion making the sweep-coverage test below vacuous."""
    assert set(encrypted_fields.CIPHERTEXT_COPIES) == _EXPECTED_CIPHERTEXT_COPIES, (
        "CIPHERTEXT_COPIES drifted from the pinned set. If you are adding or "
        "removing a stored ciphertext copy, update _EXPECTED_CIPHERTEXT_COPIES "
        "and the Phase 2 re-encrypt sweep together."
    )


def test_ciphertext_copies_reference_real_marked_cells():
    """CIPHERTEXT_COPIES (stored ciphertext copies outside their owning
    cell, e.g. front audit snapshots) must point at real storage tables and
    real marked source cells, so the Phase 2 sweep can trust the constant."""
    assert encrypted_fields.CIPHERTEXT_COPIES, (
        "CIPHERTEXT_COPIES is empty; the front-audit snapshot copies must be "
        "listed or the sweep will strand them on v1"
    )
    for storage_table, storage_path, source_cell in (
        encrypted_fields.CIPHERTEXT_COPIES
    ):
        assert storage_table in Base.metadata.tables, (
            f"CIPHERTEXT_COPIES storage table {storage_table!r} does not exist"
        )
        storage_col = storage_path.split(".", 1)[0]
        assert storage_col in Base.metadata.tables[storage_table].columns, (
            f"CIPHERTEXT_COPIES storage column "
            f"{storage_table}.{storage_col!r} does not exist"
        )
        src_table, src_column = source_cell.split(".", 1)
        physical, reason = _resolve(src_table, src_column)
        assert physical is not None, (
            f"CIPHERTEXT_COPIES source cell {source_cell!r}: {reason}"
        )


def test_marked_tables_have_single_uuid_id_primary_key():
    """Marked columns' tables use the pk convention the AAD scheme assumes.

    The registry passes a single UUID row id as the pk. If a marked table ever
    has a composite or non-`id` primary key, the AAD scheme needs the
    composite-pk extension described in the design doc.
    """
    marked_tables = {table for table, _column, _marker in _schema_marked()}
    for table_name in sorted(marked_tables):
        table = Base.metadata.tables[table_name]
        pk_cols = list(table.primary_key.columns)
        assert len(pk_cols) == 1 and pk_cols[0].name == "id", (
            f"table {table_name} does not have a single-column primary key named "
            f"'id' (found {[c.name for c in pk_cols]}); the field-encryption AAD "
            f"scheme binds ciphertext to a single UUID id. Consult {_DESIGN_DOC} "
            f"for the composite-pk extension before marking columns on this table."
        )
        pk = pk_cols[0]
        assert isinstance(pk.type, UUID), (
            f"table {table_name} primary key 'id' is {pk.type!r}, not a UUID; the "
            f"AAD scheme stringifies a UUID row id as the pk. Consult {_DESIGN_DOC}."
        )
