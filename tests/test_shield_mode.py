"""Tests for shield-mode (cf-shield opt-out + webhook).

Two layers:

1. HTTP integration against the test stack — covers the dormant path
   (settings.shield_mode_enabled=false) since that's how the test
   container is configured. Verifies the status endpoint, the user
   PATCH for the opt-out flag, and that the internal webhook is 404
   when the feature is off.

2. Unit-style tests against the service module — directly exercise
   HMAC verification, state transitions, and the mass-invalidate pass
   without depending on the FastAPI process having shield mode wired.
   Monkey-patch `settings.shield_mode_webhook_secret` for HMAC tests
   so we don't have to spin up a second container config.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import uuid

import httpx
import pytest


def _register(client: httpx.Client) -> tuple[str, str, str]:
    """Register a fresh user; return (email, password, access_token)."""
    email = f"shield-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert resp.status_code == 201
    return email, password, resp.json()["access_token"]


# ---------------------------------------------------------------------------
# HTTP-level: feature dormant on the test stack
# ---------------------------------------------------------------------------


def _stack_has_shield_mode_enabled(client: httpx.Client) -> bool:
    """Probe the running stack to see whether SHIELD_MODE_ENABLED is on.

    The two tests below assert dormant-path behaviour (feature_enabled
    is false, internal webhook 404s); they only make sense when the
    stack is in its default off-state. Operators / developers who flip
    the feature on locally (to smoketest cf-shield) end up here, so we
    detect at runtime and skip rather than fail noisily."""
    resp = client.get("/v1/shield-mode/status")
    if resp.status_code != 200:
        return False
    return bool(resp.json().get("feature_enabled"))


def test_status_endpoint_reports_feature_disabled(client: httpx.Client) -> None:
    """When shield_mode_enabled is false the status endpoint says so
    and reports active=false. No auth required."""
    if _stack_has_shield_mode_enabled(client):
        pytest.skip("stack has SHIELD_MODE_ENABLED=true; dormant-path test n/a")
    resp = client.get("/v1/shield-mode/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["feature_enabled"] is False
    assert body["active"] is False
    assert body["since"] is None


def test_internal_webhook_404_when_feature_disabled(client: httpx.Client) -> None:
    """The internal webhook must not even appear to exist when the
    feature is off; a probe gets a flat 404 rather than a hint."""
    if _stack_has_shield_mode_enabled(client):
        pytest.skip("stack has SHIELD_MODE_ENABLED=true; dormant-path test n/a")
    resp = client.post(
        "/v1/internal/shield-mode/state",
        json={"active": True},
        headers={"X-Sheaf-Signature": "deadbeef"},
    )
    assert resp.status_code == 404


def test_user_can_set_opt_out_flag(client: httpx.Client) -> None:
    """The opt-out preference persists regardless of feature_enabled.
    Selfhosters can flip it now and have it honored if they later
    migrate to an instance that runs cf-shield."""
    email, _, token = _register(client)
    auth = {"Authorization": f"Bearer {token}"}

    initial = client.get("/v1/auth/me", headers=auth).json()
    assert initial["disable_cdn_during_ddos"] is False

    patched = client.patch(
        "/v1/auth/me",
        headers=auth,
        json={"disable_cdn_during_ddos": True},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["disable_cdn_during_ddos"] is True

    # Round-trip via a fresh GET to confirm persistence.
    refreshed = client.get("/v1/auth/me", headers=auth).json()
    assert refreshed["disable_cdn_during_ddos"] is True


def test_account_export_includes_opt_out_field(client: httpx.Client) -> None:
    """Article-15 account dump surfaces the new preference too. POST
    /v1/account/data is the right endpoint (it requires a password
    confirm; we hand it the registration password)."""
    email, password, token = _register(client)
    auth = {"Authorization": f"Bearer {token}"}

    client.patch(
        "/v1/auth/me",
        headers=auth,
        json={"disable_cdn_during_ddos": True},
    )

    resp = client.post(
        "/v1/account/data",
        headers=auth,
        json={"password": password},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["account"]["disable_cdn_during_ddos"] is True


# ---------------------------------------------------------------------------
# Unit-level: service module exercised directly
# ---------------------------------------------------------------------------


def test_verify_signature_constant_time_match(monkeypatch: pytest.MonkeyPatch) -> None:
    from sheaf.config import settings
    from sheaf.services.shield_mode import verify_signature

    monkeypatch.setattr(settings, "shield_mode_webhook_secret", "topsecret")
    body = b'{"active": true}'
    good = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()
    bad = "00" * 32

    assert verify_signature(body, good) is True
    assert verify_signature(body, bad) is False
    assert verify_signature(body, None) is False
    assert verify_signature(body, "") is False


def test_verify_signature_refuses_when_secret_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty secret should never accept a signature, even an empty one.
    Prevents a foot-gun where a misconfigured deploy would silently
    accept anything."""
    from sheaf.config import settings
    from sheaf.services.shield_mode import verify_signature

    monkeypatch.setattr(settings, "shield_mode_webhook_secret", "")
    body = b"{}"
    assert verify_signature(body, "") is False
    assert verify_signature(body, hmac.new(b"", body, hashlib.sha256).hexdigest()) is False


def _async_db_session():
    """Build an AsyncSession bound to the test DB. Yields a session;
    caller closes it. Mirrors the pattern in test_system_safety.py."""
    from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
    from sqlalchemy.orm import sessionmaker

    from sheaf.config import settings

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    engine = create_async_engine(db_url)
    session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, session


def _reset_redis_singleton() -> None:
    """`sheaf.auth.sessions` caches the Redis client in a module global
    bound to whatever event loop first touched it. asyncio.run() makes
    a fresh loop per call, so the cached client points at a closed
    loop on the second call. Reset before each test that builds its
    own loop."""
    import sheaf.auth.sessions as sessions_mod

    sessions_mod._redis = None


def _patch_redis_url_for_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `settings.redis_url` at the test stack's host-mapped port.

    The default redis_url is `redis://redis:6379/0`, which only resolves
    from inside the docker-compose network. Tests that exercise the
    Redis client directly (rather than going through the HTTP app) need
    the host-port-mapped URL instead. run_tests.sh exports this as
    SHEAF_TEST_REDIS_URL; when invoked manually, the default falls back
    to the compose convention (localhost:6380)."""
    from sheaf.config import settings

    test_redis_url = (
        os.environ.get("SHEAF_TEST_REDIS_URL") or "redis://localhost:6380/0"
    )
    monkeypatch.setattr(settings, "redis_url", test_redis_url)


def test_apply_transition_clears_redis_state_after_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A down transition writes active=false. A subsequent up writes
    active=true with a fresh `since`. Re-applying the same state is a
    no-op (since does not get rewritten)."""
    from sheaf.services.shield_mode import (
        ShieldState,
        _write_state,
        apply_transition,
        get_state,
    )

    _patch_redis_url_for_host(monkeypatch)
    _reset_redis_singleton()

    async def _run() -> None:
        engine, session = _async_db_session()
        try:
            # Clear any leftover state from a previous test.
            await _write_state(ShieldState(active=False, since=None))
            async with session() as db:
                up = await apply_transition(active=True, db=db)
                assert up.active is True
                first_since = up.since
                assert first_since is not None

                # Re-apply up: idempotent.
                still_up = await apply_transition(active=True, db=db)
                assert still_up.active is True
                assert still_up.since == first_since

                # Down clears.
                down = await apply_transition(active=False, db=db)
                assert down.active is False

                # Confirm get_state reads what was written.
                read = await get_state()
                assert read.active is False
        finally:
            # Leave Redis in a clean state for the next test.
            await _write_state(ShieldState(active=False, since=None))
            await engine.dispose()

    asyncio.run(_run())


def test_mass_invalidate_only_touches_opted_out_users(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An up transition deletes sessions for users with the flag set
    and leaves everyone else's sessions alone."""
    from sqlalchemy import select

    from sheaf.auth.sessions import (
        create_session,
        get_session_info,
    )
    from sheaf.crypto import blind_index
    from sheaf.models.user import User
    from sheaf.services.shield_mode import (
        ShieldState,
        _write_state,
        apply_transition,
    )

    _patch_redis_url_for_host(monkeypatch)
    _reset_redis_singleton()

    # Two fresh users — one opted out, one not. Register via HTTP so
    # everything (encryption keys, blind index) goes through the
    # normal flow.
    with httpx.Client(base_url=os.environ["SHEAF_TEST_URL"]) as client:
        opted_email = f"shield-opt-{uuid.uuid4().hex[:8]}@sheaf.dev"
        stay_email = f"shield-stay-{uuid.uuid4().hex[:8]}@sheaf.dev"
        client.post(
            "/v1/auth/register",
            json={"email": opted_email, "password": "testpassword123"},
        )
        client.post(
            "/v1/auth/register",
            json={"email": stay_email, "password": "testpassword123"},
        )

    async def _run() -> str:
        engine, session = _async_db_session()
        try:
            await _write_state(ShieldState(active=False, since=None))

            # Mint a session for each user directly so we have something
            # to invalidate without going through login.
            async with session() as db:
                opted = (
                    await db.execute(
                        select(User).where(User.email_hash == blind_index(opted_email))
                    )
                ).scalar_one()
                stay = (
                    await db.execute(
                        select(User).where(User.email_hash == blind_index(stay_email))
                    )
                ).scalar_one()
                opted.disable_cdn_during_ddos = True
                await db.commit()
                opted_sid = await create_session(
                    opted.id, user_agent="pytest", ip="127.0.0.1"
                )
                stay_sid = await create_session(
                    stay.id, user_agent="pytest", ip="127.0.0.1"
                )

                await apply_transition(active=True, db=db)

            # Opted-out session should be gone, the other intact.
            opted_present = await get_session_info(opted_sid) is not None
            stay_present = await get_session_info(stay_sid) is not None
            return (
                f"opted_sid={opted_sid} present={opted_present}, "
                f"stay_sid={stay_sid} present={stay_present}"
            )
        finally:
            await _write_state(ShieldState(active=False, since=None))
            await engine.dispose()

    summary = asyncio.run(_run())
    assert "opted_sid=" in summary
    # Parse: opted should be gone, stay should remain. Format above.
    parts = dict(p.split(" present=") for p in summary.split(", "))
    opted_present = parts[next(k for k in parts if k.startswith("opted_sid="))]
    stay_present = parts[next(k for k in parts if k.startswith("stay_sid="))]
    assert opted_present == "False", f"opted-out session should be revoked: {summary}"
    assert stay_present == "True", f"non-opted-out session should remain: {summary}"
