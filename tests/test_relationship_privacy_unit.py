"""Unit tests for the per-edge relationship privacy primitives.

Pure logic, no DB and no docker stack. What is decidable on the host: the
importer's visibility coercer (an untrusted enum string out of an uploaded
file), its demotion of an edge that arrives already public, the schemas that
carry the step-up credentials, and the declared defaults / caps the rest of the
feature leans on. The end-to-end behaviour (deferral, the finalize sweep, the
projection) lives in test_relationship_privacy.py and
test_public_profiles_api.py.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from sheaf.api.v1.relationships import _apply_orientation
from sheaf.models.relationship import (
    GroupRelationship,
    MemberRelationship,
    RelationshipSymmetry,
    RelationshipType,
)
from sheaf.models.share import ShareView
from sheaf.models.system import PrivacyLevel
from sheaf.schemas.public_profile import (
    PublicRelationship,
    PublicRelationshipEndpoint,
)
from sheaf.schemas.relationship import (
    RelationshipEdgeCreate,
    RelationshipEdgeUpdate,
)
from sheaf.services import import_limits as il
from sheaf.services.import_content_dedup import PairGuard
from sheaf.services.sharing import EXPOSURE_FLAGS, promote_view_flags
from sheaf.services.sheaf_import import _import_relationship_edges, _rel_visibility

# --- Importer coercion ------------------------------------------------------


@pytest.mark.parametrize("level", ["private", "friends", "public"])
def test_rel_visibility_accepts_every_level(level: str):
    assert _rel_visibility(level) == PrivacyLevel(level)


@pytest.mark.parametrize(
    "garbage",
    [
        None,
        "",
        "PUBLIC",  # case matters; the enum values are lower-case
        "publik",
        "everyone",
        42,
        True,
        pytest.param(b"public", id="bytes-public"),
        ["public"],
        {"visibility": "public"},
    ],
)
def test_rel_visibility_falls_back_to_private_on_garbage(garbage: object):
    """A garbled file must fail towards "too private", never "published" - an
    edge names two people at once, so a coercion that guessed wrong publishes
    a relationship neither of them agreed to."""
    assert _rel_visibility(garbage) == PrivacyLevel.PRIVATE


# --- Importer demotion ------------------------------------------------------


class _Recorder:
    """Stands in for the session. `_import_relationship_edges` only ever calls
    `add` (the caller flushes), so the rows it would enqueue are the result."""

    def __init__(self):
        self.added: list = []

    def add(self, row) -> None:
        self.added.append(row)


def _import_one_edge(*, visibility: str, exposed: str | None):
    """Import a single edge at `visibility`, with `exposed` choosing which
    endpoints `relationship_exposed_member_ids` would have returned: "both",
    "one", "none", or None for the group path, which passes no set at all.

    Returns (imported, skipped, demoted, stored_row).
    """
    source = SimpleNamespace(id=uuid.uuid4())
    target = SimpleNamespace(id=uuid.uuid4())
    exposed_ids = {
        "both": {source.id, target.id},
        "one": {source.id},
        "none": set(),
        None: None,
    }[exposed]
    db = _Recorder()
    imported, skipped, demoted = _import_relationship_edges(
        db,
        SimpleNamespace(id=uuid.uuid4()),
        [
            {
                "source_id": "m1",
                "target_id": "m2",
                "relationship_type_id": "rt1",
                "visibility": visibility,
            }
        ],
        endpoint_map={"m1": source, "m2": target},
        type_map={
            "rt1": SimpleNamespace(
                id=uuid.uuid4(), symmetry=RelationshipSymmetry.SYMMETRIC
            )
        },
        model=MemberRelationship,
        guard=PairGuard(),
        exposed_ids=exposed_ids,
    )
    assert len(db.added) == 1
    return imported, skipped, demoted, db.added[0]


def test_import_demotes_a_public_edge_between_two_exposed_members():
    """The file says public and both endpoints are already being published
    through a shared view, so storing that level as-is would publish the edge
    the instant the import commits - with no step-up and no grace window."""
    imported, skipped, demoted, row = _import_one_edge(
        visibility="public", exposed="both"
    )
    assert (imported, skipped, demoted) == (1, 0, 1)
    assert row.visibility == PrivacyLevel.PRIVATE


@pytest.mark.parametrize("exposed", ["one", "none"])
def test_import_keeps_a_public_edge_that_would_not_be_published(exposed: str):
    """The guard must not flatten every import: with either end unexposed the
    edge is drawn for nobody, so the file's level stands."""
    imported, skipped, demoted, row = _import_one_edge(
        visibility="public", exposed=exposed
    )
    assert (imported, skipped, demoted) == (1, 0, 0)
    assert row.visibility == PrivacyLevel.PUBLIC


def test_group_edges_are_never_demoted():
    """The group path passes no set at all - nothing projects group edges, so
    there is nothing an imported one could publish."""
    _, _, demoted, row = _import_one_edge(visibility="public", exposed=None)
    assert demoted == 0
    assert row.visibility == PrivacyLevel.PUBLIC


@pytest.mark.parametrize("level", ["private", "friends"])
def test_import_below_public_is_not_counted_as_a_demotion(level: str):
    """Only `public` is served, so a lower level was never a demotion and must
    not be reported to the user as one."""
    _, _, demoted, row = _import_one_edge(visibility=level, exposed="both")
    assert demoted == 0
    assert row.visibility == PrivacyLevel(level)


# --- Schemas ----------------------------------------------------------------


def test_edge_create_defaults_to_private_and_can_carry_step_up():
    body = RelationshipEdgeCreate(
        source_id="11111111-1111-1111-1111-111111111111",
        target_id="22222222-2222-2222-2222-222222222222",
        relationship_type_id="33333333-3333-3333-3333-333333333333",
    )
    assert body.visibility == PrivacyLevel.PRIVATE
    # Creating an edge already public is the same exposure as raising one, so
    # the create body takes the same credentials the update body does.
    assert body.password is None and body.totp_code is None
    assert {"password", "totp_code"} <= set(RelationshipEdgeCreate.model_fields)


def test_edge_update_rejects_an_unknown_level():
    with pytest.raises(ValidationError):
        RelationshipEdgeUpdate(visibility="everyone")


def test_edge_update_carries_step_up_credentials():
    """The credentials ride on the same body as the level, and are absent (not
    empty strings) when they were not supplied - the endpoint pops them before
    anything iterates the update, so they can never reach a column."""
    body = RelationshipEdgeUpdate(visibility="public", password="hunter2")
    assert body.visibility == PrivacyLevel.PUBLIC
    assert body.password == "hunter2"
    assert body.totp_code is None
    assert RelationshipEdgeUpdate().model_dump(exclude_unset=True) == {}


def test_edge_update_carries_flip_and_mutual_only_when_asked():
    """Both are tri-state on the wire: absent means "leave it alone", which is
    what lets one PATCH body change privacy without silently restating the
    direction (or the other way round)."""
    assert RelationshipEdgeUpdate().flip is None
    assert RelationshipEdgeUpdate().mutual is None
    body = RelationshipEdgeUpdate(flip=True, mutual=False)
    assert body.model_dump(exclude_unset=True) == {"flip": True, "mutual": False}


# --- Orientation (flip / mutual) --------------------------------------------


class _TypeLookup:
    """Stands in for the session: `_get_type_in_system` is the only query
    `_apply_orientation` makes, and it wants one scalar back."""

    def __init__(self, rtype):
        self.rtype = rtype

    async def execute(self, *_args, **_kwargs):
        return SimpleNamespace(scalar_one_or_none=lambda: self.rtype)


class _NoQueries:
    """A session that fails the test if it is touched at all."""

    async def execute(self, *_args, **_kwargs):  # pragma: no cover - guard
        raise AssertionError("orientation asked the DB for something")


_STAGED_AT = datetime(2030, 1, 1, tzinfo=UTC)


def _orientable_edge(symmetry: RelationshipSymmetry, *, mutual: bool = False):
    """An edge row with a raise already staged on it, so every orientation test
    also pins that the staging is left alone."""
    rtype = SimpleNamespace(id=uuid.uuid4(), symmetry=symmetry)
    edge = SimpleNamespace(
        source_id=uuid.uuid4(),
        target_id=uuid.uuid4(),
        relationship_type_id=rtype.id,
        mutual=mutual,
        visibility=PrivacyLevel.PRIVATE,
        pending_visibility=PrivacyLevel.PUBLIC,
        visibility_activates_at=_STAGED_AT,
    )
    return edge, _TypeLookup(rtype)


async def _orient(edge, db, **kw) -> bool:
    return await _apply_orientation(
        edge, system=SimpleNamespace(id=uuid.uuid4()), db=db, **kw
    )


async def test_flip_swaps_the_endpoints_of_a_directional_edge():
    edge, db = _orientable_edge(RelationshipSymmetry.DIRECTIONAL)
    was = (edge.source_id, edge.target_id)

    assert await _orient(edge, db, flip=True, mutual=None) is True
    assert (edge.source_id, edge.target_id) == (was[1], was[0])


async def test_flip_is_refused_on_a_symmetric_type():
    """Nothing to reverse, and the stored order is the canonical one the
    uniqueness index leans on - so this is a 400, not a quiet no-op."""
    edge, db = _orientable_edge(RelationshipSymmetry.SYMMETRIC)
    was = (edge.source_id, edge.target_id)

    with pytest.raises(HTTPException) as err:
        await _orient(edge, db, flip=True, mutual=None)
    assert err.value.status_code == 400
    assert (edge.source_id, edge.target_id) == was


async def test_flip_leaves_a_staged_raise_alone():
    """Reorienting an edge is not a way to cancel a pending publication, or to
    hurry one along."""
    edge, db = _orientable_edge(RelationshipSymmetry.EITHER)

    await _orient(edge, db, flip=True, mutual=None)

    assert edge.visibility == PrivacyLevel.PRIVATE
    assert edge.pending_visibility == PrivacyLevel.PUBLIC
    assert edge.visibility_activates_at == _STAGED_AT


@pytest.mark.parametrize(
    "symmetry",
    [RelationshipSymmetry.SYMMETRIC, RelationshipSymmetry.DIRECTIONAL],
)
async def test_mutual_is_normalised_off_for_non_either_types(
    symmetry: RelationshipSymmetry,
):
    """Same normalisation create does: `mutual` only means anything on an
    `either` type, and false is what the row would have meant anyway."""
    edge, db = _orientable_edge(symmetry)

    assert await _orient(edge, db, flip=None, mutual=True) is False
    assert edge.mutual is False


async def test_mutual_toggles_both_ways_on_an_either_type():
    edge, db = _orientable_edge(RelationshipSymmetry.EITHER)

    assert await _orient(edge, db, flip=None, mutual=True) is True
    assert edge.mutual is True
    assert await _orient(edge, db, flip=None, mutual=False) is True
    assert edge.mutual is False
    # Asking for what is already stored is not a change.
    assert await _orient(edge, db, flip=None, mutual=False) is False


async def test_a_privacy_only_update_never_looks_up_the_type():
    """A PATCH that says nothing about orientation must not pay for a type
    fetch, and must report that it changed nothing."""
    edge, _ = _orientable_edge(RelationshipSymmetry.DIRECTIONAL)

    assert await _orient(edge, _NoQueries(), flip=None, mutual=None) is False
    assert await _orient(edge, _NoQueries(), flip=False, mutual=None) is False


def test_public_relationship_key_sets():
    """Host-side half of the fail-closed contract pinned end-to-end in
    test_public_profiles_api.py."""
    assert set(PublicRelationship.model_fields) == {
        "id", "type_name", "type_color", "source", "target",
        "source_label", "target_label", "mutual",
    }
    assert set(PublicRelationshipEndpoint.model_fields) == {"id", "name"}


# --- Declared defaults and caps ---------------------------------------------


def test_edges_are_private_by_default():
    for model in (MemberRelationship, GroupRelationship):
        assert model.__table__.c.visibility.default.arg == PrivacyLevel.PRIVATE


def test_only_member_edges_can_stage_a_raise():
    """Nothing projects group edges, so there is no exposure to wait out and no
    staging columns to wait it out with."""
    assert "pending_visibility" in MemberRelationship.__table__.c
    assert "visibility_activates_at" in MemberRelationship.__table__.c
    assert "pending_visibility" not in GroupRelationship.__table__.c
    assert "visibility_activates_at" not in GroupRelationship.__table__.c


def test_relationship_type_color_cap_matches_the_column():
    assert il.REL_TYPE_COLOR.limit == RelationshipType.__table__.c.color.type.length


def test_include_relationships_is_promoted_like_any_other_exposure_flag():
    assert "include_relationships" in EXPOSURE_FLAGS

    view = ShareView(name="V", include_relationships=False)
    view.pending_include_relationships = True
    promote_view_flags(view)
    assert view.include_relationships is True
    assert view.pending_include_relationships is None
    assert view.flags_activate_at is None
