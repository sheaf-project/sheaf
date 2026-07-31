"""Unit tests for the share exposure lifecycle.

Pure logic only (no DB, no HTTP): these pin the asymmetry the whole feature
rests on - exposing waits out the grace window, un-exposing never does - plus
the token handling. The DB-backed paths (grant resolution, the finalize sweep)
are covered by the behavioural suite.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from sheaf.crypto import hash_share_token
from sheaf.models.share import (
    ShareGrant,
    ShareGrantStatus,
    ShareItemStatus,
    ShareSubjectType,
)
from sheaf.models.system import System
from sheaf.models.user import User
from sheaf.services.sharing import (
    _activation,
    is_exposure_safeguarded,
    require_adult_attestation,
    revoke_grant,
    rotate_grant_token,
)


def _system(*, grace: int = 0, category: bool = False) -> System:
    return System(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        name="test",
        safety_grace_period_days=grace,
        safety_applies_to_profile_visibility=category,
    )


def _link_grant() -> ShareGrant:
    return ShareGrant(
        id=uuid.uuid4(),
        system_id=uuid.uuid4(),
        view_id=uuid.uuid4(),
        subject_type=ShareSubjectType.LINK.value,
        token_hash=hash_share_token("original-token"),
        status=ShareGrantStatus.ACTIVE.value,
        created_at=datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Safeguard predicate
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("grace", "category", "expected"),
    [
        (0, False, False),
        (0, True, False),  # category on but no grace window to serve
        (7, False, False),  # grace set but the category is not armed
        (7, True, True),
    ],
)
def test_is_exposure_safeguarded(grace, category, expected):
    assert is_exposure_safeguarded(_system(grace=grace, category=category)) is expected


# ---------------------------------------------------------------------------
# Activation lifecycle
# ---------------------------------------------------------------------------


def test_adding_to_an_unshared_view_is_immediate():
    """No grant points at the view, so adding a member exposes nothing."""
    status, activates_at = _activation(
        _system(grace=7, category=True), already_shared=False
    )
    assert status == ShareItemStatus.ACTIVE.value
    assert activates_at is None


def test_adding_to_a_shared_view_waits_out_the_grace_window():
    system = _system(grace=7, category=True)
    before = datetime.now(UTC)

    status, activates_at = _activation(system, already_shared=True)

    assert status == ShareItemStatus.PENDING.value
    assert activates_at is not None
    # Roughly 7 days out; generous bounds so the test is not clock-flaky.
    assert before + timedelta(days=6, hours=23) < activates_at
    assert activates_at < before + timedelta(days=7, minutes=1)


def test_shared_view_without_the_category_armed_is_immediate():
    """The grace window only applies when the user opted this category in."""
    status, activates_at = _activation(
        _system(grace=7, category=False), already_shared=True
    )
    assert status == ShareItemStatus.ACTIVE.value
    assert activates_at is None


# ---------------------------------------------------------------------------
# Revoke / rotate: always immediate
# ---------------------------------------------------------------------------


def test_revoke_is_immediate_and_idempotent():
    grant = _link_grant()

    revoke_grant(grant)
    first_revoked_at = grant.revoked_at

    assert grant.status == ShareGrantStatus.REVOKED.value
    assert first_revoked_at is not None

    # Revoking again must not move the timestamp or error.
    revoke_grant(grant)
    assert grant.revoked_at == first_revoked_at
    assert grant.status == ShareGrantStatus.REVOKED.value


def test_revoke_ignores_the_grace_window_entirely():
    """Going dark is never delayed, whatever the safety settings say."""
    grant = _link_grant()
    grant.status = ShareGrantStatus.PENDING.value
    grant.activates_at = datetime.now(UTC) + timedelta(days=7)

    revoke_grant(grant)

    assert grant.status == ShareGrantStatus.REVOKED.value
    assert grant.revoked_at is not None


def test_rotate_replaces_the_token_so_the_old_url_dies():
    grant = _link_grant()
    old_hash = grant.token_hash

    new_token = rotate_grant_token(grant)

    assert grant.token_hash != old_hash
    assert grant.token_hash == hash_share_token(new_token)
    # The previous token no longer resolves to this grant.
    assert hash_share_token("original-token") != grant.token_hash


def test_rotate_rejects_a_public_grant():
    grant = _link_grant()
    grant.subject_type = ShareSubjectType.PUBLIC.value

    with pytest.raises(HTTPException) as exc:
        rotate_grant_token(grant)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# Tokens
# ---------------------------------------------------------------------------


def test_token_hash_is_deterministic_and_distinct():
    assert hash_share_token("abc") == hash_share_token("abc")
    assert hash_share_token("abc") != hash_share_token("abd")


def test_rotated_tokens_are_unique_per_call():
    grant = _link_grant()
    tokens = {rotate_grant_token(grant) for _ in range(10)}
    assert len(tokens) == 10


# ---------------------------------------------------------------------------
# Attestation gate
# ---------------------------------------------------------------------------


def test_attestation_required_before_publishing():
    user = User(id=uuid.uuid4(), email="x", email_hash="x", password_hash="x")
    user.adult_attested_at = None

    with pytest.raises(HTTPException) as exc:
        require_adult_attestation(user)
    assert exc.value.status_code == 403


def test_attested_user_may_publish():
    user = User(id=uuid.uuid4(), email="x", email_hash="x", password_hash="x")
    user.adult_attested_at = datetime.now(UTC)

    require_adult_attestation(user)  # must not raise
