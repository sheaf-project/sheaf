"""Pure-Python coverage for the relationship-edge engine
(`sheaf.services.relationships`). Runs headless. These cases are the contract
the TypeScript mirror (`web/src/lib/relationships.ts`) must match exactly -
keep them in sync."""

from __future__ import annotations

import uuid

from sheaf.models.relationship import RelationshipSymmetry as S
from sheaf.services.relationships import (
    canonicalize_pair,
    endpoint_labels,
    resolve_label,
)

# Two fixed uuids with a known order (A < B) so canonicalisation is testable.
A = uuid.UUID("00000000-0000-0000-0000-0000000000aa")
B = uuid.UUID("00000000-0000-0000-0000-0000000000bb")
assert A < B


# --- canonicalize_pair -----------------------------------------------------

def test_canonicalize_symmetric_orders_smaller_first():
    assert canonicalize_pair(S.SYMMETRIC, B, A) == (A, B)
    assert canonicalize_pair(S.SYMMETRIC, A, B) == (A, B)


def test_canonicalize_directional_preserves_order():
    # Direction is load-bearing; never reorder.
    assert canonicalize_pair(S.DIRECTIONAL, B, A) == (B, A)
    assert canonicalize_pair(S.EITHER, B, A) == (B, A)


# --- resolve_label ---------------------------------------------------------

def test_symmetric_both_sides_read_forward():
    kw = dict(
        symmetry=S.SYMMETRIC,
        forward_label="partner",
        reverse_label=None,
        mutual=False,
        source_id=A,
    )
    assert resolve_label(**kw, viewpoint_id=A) == ("partner", "none")
    assert resolve_label(**kw, viewpoint_id=B) == ("partner", "none")


def test_directional_source_vs_target():
    kw = dict(
        symmetry=S.DIRECTIONAL,
        forward_label="parent",
        reverse_label="child",
        mutual=False,
        source_id=A,  # A is the parent
    )
    assert resolve_label(**kw, viewpoint_id=A) == ("parent", "outgoing")
    assert resolve_label(**kw, viewpoint_id=B) == ("child", "incoming")


def test_either_directional_uses_forward_reverse():
    kw = dict(
        symmetry=S.EITHER,
        forward_label="protector",
        reverse_label="protectee",
        mutual=False,
        source_id=A,  # A protects B
    )
    assert resolve_label(**kw, viewpoint_id=A) == ("protector", "outgoing")
    assert resolve_label(**kw, viewpoint_id=B) == ("protectee", "incoming")


def test_either_mutual_both_sides_read_forward():
    kw = dict(
        symmetry=S.EITHER,
        forward_label="protector",
        reverse_label="protectee",
        mutual=True,
        source_id=A,
    )
    assert resolve_label(**kw, viewpoint_id=A) == ("protector", "none")
    assert resolve_label(**kw, viewpoint_id=B) == ("protector", "none")


def test_directional_missing_reverse_falls_back_to_forward():
    # Schema requires reverse_label for directional, but the engine is defensive.
    label, direction = resolve_label(
        symmetry=S.DIRECTIONAL,
        forward_label="parent",
        reverse_label=None,
        mutual=False,
        source_id=A,
        viewpoint_id=B,
    )
    assert label == "parent"
    assert direction == "incoming"


# --- endpoint_labels -------------------------------------------------------

def test_endpoint_labels_all_modes():
    assert endpoint_labels(
        symmetry=S.SYMMETRIC, forward_label="partner", reverse_label=None,
        mutual=False,
    ) == ("partner", "partner")
    assert endpoint_labels(
        symmetry=S.DIRECTIONAL, forward_label="parent", reverse_label="child",
        mutual=False,
    ) == ("parent", "child")
    assert endpoint_labels(
        symmetry=S.EITHER, forward_label="protector", reverse_label="protectee",
        mutual=False,
    ) == ("protector", "protectee")
    assert endpoint_labels(
        symmetry=S.EITHER, forward_label="protector", reverse_label="protectee",
        mutual=True,
    ) == ("protector", "protector")
