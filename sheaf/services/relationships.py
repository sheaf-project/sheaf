"""Shared relationship-edge logic: the ONE source of truth for how a stored
edge is read from each endpoint, and how a symmetric pair is canonicalised.

Used by the member + group edge serializers, the graph endpoint, and the
importer's canonicalisation. Mirrored in TypeScript at
`web/src/lib/relationships.ts` - keep the two in lockstep; both are pinned by
the same table-driven cases (`tests/test_relationships_engine.py`).

Only ONE canonical row is ever stored per relationship; the inverse is derived
here at read time. `source_id` is the `forward_label` endpoint for
directional / either edges (order is load-bearing); for symmetric edges order
is meaningless and `canonicalize_pair` normalises it so dedup + rendering are
stable.
"""

from __future__ import annotations

import uuid
from typing import Literal

from sheaf.models.relationship import RelationshipSymmetry

# From a given member/group's point of view, an edge points away from them
# (they are the source of a directional edge), toward them (they are the
# target), or has no direction (symmetric, or an either-edge marked mutual).
Direction = Literal["none", "outgoing", "incoming"]


def _is_undirected(symmetry: RelationshipSymmetry, mutual: bool) -> bool:
    return symmetry == RelationshipSymmetry.SYMMETRIC or (
        symmetry == RelationshipSymmetry.EITHER and mutual
    )


def canonicalize_pair(
    symmetry: RelationshipSymmetry,
    source_id: uuid.UUID,
    target_id: uuid.UUID,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Return (source, target) to store.

    Symmetric edges are normalised so the smaller uuid is source, matching the
    `least()/greatest()` functional unique index (Postgres uuid ordering and
    Python `uuid.UUID` ordering agree, both being big-endian byte order).
    Directional / either edges keep the caller's order - source is the
    forward-label endpoint and must be preserved.
    """
    if symmetry == RelationshipSymmetry.SYMMETRIC and source_id > target_id:
        return target_id, source_id
    return source_id, target_id


def resolve_label(
    *,
    symmetry: RelationshipSymmetry,
    forward_label: str,
    reverse_label: str | None,
    mutual: bool,
    source_id: uuid.UUID,
    viewpoint_id: uuid.UUID,
) -> tuple[str, Direction]:
    """The (label, direction) this edge reads as from `viewpoint_id`'s side.

    - undirected (symmetric, or either+mutual): forward_label, "none".
    - otherwise: the source endpoint reads forward_label ("outgoing"), the
      target endpoint reads reverse_label ("incoming"). reverse_label falls
      back to forward_label defensively (schema requires it for these modes).
    """
    if _is_undirected(symmetry, mutual):
        return forward_label, "none"
    if viewpoint_id == source_id:
        return forward_label, "outgoing"
    return (reverse_label or forward_label), "incoming"


def endpoint_labels(
    *,
    symmetry: RelationshipSymmetry,
    forward_label: str,
    reverse_label: str | None,
    mutual: bool,
) -> tuple[str, str]:
    """(source_label, target_label) for an edge, independent of viewpoint.

    Convenience for the graph endpoint, which labels both ends of each edge at
    once. Undirected edges read forward_label on both ends.
    """
    if _is_undirected(symmetry, mutual):
        return forward_label, forward_label
    return forward_label, (reverse_label or forward_label)
