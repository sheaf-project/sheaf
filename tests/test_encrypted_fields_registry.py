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
    Rule: exact column match, OR the logical name starts with
    `physical_column + "."` and that physical column is marked
    info={"encrypted": "json"}.
    """
    t = Base.metadata.tables.get(table)
    if t is None:
        return None, f"table {table!r} does not exist in Base.metadata"
    if logical in t.columns:
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
