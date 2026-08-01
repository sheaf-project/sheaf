#!/usr/bin/env python3
"""Static sanity check for the alembic migration graph.

Runs with no dependencies (stdlib only) so it can live in the fast lint stage,
and - crucially - it runs against the PR *merge* result in CI, which is where a
cross-PR collision shows up (two branches numbering a migration off the same
head, or picking the same revision id).

Two failure modes it catches, both of which otherwise only surface as a
2-minute "app did not become ready" timeout when alembic refuses to pick a head:

  1. Duplicate revision id  - two files declaring the same `revision`.
  2. Multiple heads         - a revision that nothing chains onto, more than
                              once, i.e. the graph forked (usually two
                              migrations sharing a `down_revision`).

Exit non-zero with a pointer to the fix (renumber to the next id in sequence and
re-point `down_revision` at the current head).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSIONS = Path(__file__).resolve().parent.parent / "alembic" / "versions"

# Module-level assignments only (column 0), so a `revision` mentioned in a
# docstring or comment is ignored.
_REVISION = re.compile(r"^revision(?::[^=\n]+)?\s*=\s*[\"']([^\"']+)[\"']", re.M)
# down_revision RHS can be None, a single quoted id, or a tuple of ids (merge
# points). Grab every quoted id on the assignment line.
_DOWN = re.compile(r"^down_revision(?::[^=\n]+)?\s*=\s*(.+)$", re.M)
_QUOTED = re.compile(r"[\"']([^\"']+)[\"']")


def main() -> int:
    files = sorted(VERSIONS.glob("*.py"))
    revisions: dict[str, list[str]] = {}
    referenced: set[str] = set()

    for path in files:
        text = path.read_text(encoding="utf-8")
        rev_match = _REVISION.search(text)
        if not rev_match:
            print(f"WARN: {path.name} has no revision id; skipping", file=sys.stderr)
            continue
        revisions.setdefault(rev_match.group(1), []).append(path.name)

        down_match = _DOWN.search(text)
        if down_match:
            referenced.update(_QUOTED.findall(down_match.group(1)))

    errors: list[str] = []

    dupes = {rev: names for rev, names in revisions.items() if len(names) > 1}
    for rev, names in dupes.items():
        errors.append(f"duplicate revision id {rev!r} in: {', '.join(names)}")

    heads = sorted(rev for rev in revisions if rev not in referenced)
    if len(heads) > 1:
        errors.append(
            "multiple alembic heads: "
            + ", ".join(heads)
            + " (the migration graph forked - two migrations likely share a "
            "down_revision)"
        )

    if errors:
        print("Migration graph check FAILED:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        print(
            "\nFix: renumber your migration to the next id in sequence and set "
            "its down_revision to the current head, so the graph is a single "
            "linear chain.",
            file=sys.stderr,
        )
        return 1

    if not heads:
        print("Migration graph check FAILED: no head found (empty or cyclic).", file=sys.stderr)
        return 1

    print(f"Migration graph OK: {len(revisions)} revisions, single head {heads[0]}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
