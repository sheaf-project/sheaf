"""Field-encryption AAD binding: relocation attacks must fail closed.

Every v2 encrypted cell is bound to its (table, column, row-pk) via the
associated data in `sheaf.crypto.field_aad` (see `sheaf.encrypted_fields`
for the per-cell registry). The Poly1305 tag on a legacy v1 ciphertext
authenticates the bytes but not where they live, so a DB-write attacker
could lift a ciphertext out of one cell and drop it into another and it
would still decrypt to attacker-chosen plaintext. v2 closes that: a
ciphertext moved to a different row or column decrypts to a nacl
CryptoError instead of the wrong plaintext.

These tests drive that guarantee at the API level. Each one simulates the
attacker with a raw SQL UPDATE against the test database (the same
`_test_engine` direct-DB pattern the job-runner tests use), then reads the
victim record back through the HTTP API and asserts the attacker's
plaintext never comes back. Whether the read fails closed with a 5xx (the
decrypt raises) or a tolerant path substitutes a placeholder is left open
on purpose: the invariant under test is "no attacker-chosen plaintext is
returned", not the specific error shape.

The final test pins the dual-read window: an old, unprefixed v1 row still
decrypts normally, so converting a call site to v2 does not orphan history
written before the conversion.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pyotp
from sqlalchemy import text

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")
PASSWORD = "testpassword123"


def _test_engine():
    from sqlalchemy.ext.asyncio import create_async_engine

    from sheaf.config import settings

    db_url = os.environ.get("SHEAF_TEST_DB_URL") or settings.database_url
    return create_async_engine(db_url)


def _run_sql(statement: str, **params) -> int:
    """Run one UPDATE as the simulated DB-write attacker and commit it.

    Uses a fresh engine per call and disposes it, matching the direct-DB
    helpers in test_job_runner_rework.py so these tests stay self-contained.
    Returns the statement's rowcount: every attacker-UPDATE call site asserts
    it is 1, so a typo'd id/table that matches zero rows cannot let the test
    pass vacuously (the "attack" would never have landed).
    """

    async def run() -> int:
        engine = _test_engine()
        try:
            async with engine.begin() as conn:
                result = await conn.execute(text(statement), params)
                return result.rowcount
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _fetch_one(statement: str, **params):
    """Run a single-row, single-column SELECT and return the value.

    Used to read a victim cell back after a swap and prove the attacker's
    ciphertext actually landed there before asserting the API refuses it.
    """

    async def run():
        engine = _test_engine()
        try:
            async with engine.connect() as conn:
                result = await conn.execute(text(statement), params)
                return result.scalar_one()
        finally:
            await engine.dispose()

    return asyncio.run(run())


def _register(client: httpx.Client, email: str) -> str:
    """Register a user on `client` and pin its bearer token. Returns user id."""
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": PASSWORD},
    )
    assert resp.status_code == 201, resp.text
    client.headers["Authorization"] = f"Bearer {resp.json()['access_token']}"
    return client.get("/v1/auth/me").json()["id"]


# ---------------------------------------------------------------------------
# 1. Same-column, cross-row swap (members.name)
# ---------------------------------------------------------------------------


def test_member_name_cross_row_swap_fails_closed(auth_client: httpx.Client):
    """Attacker copies member A's `name` ciphertext onto victim member B's
    row at the DB layer. A's name ciphertext is bound to A's pk, so reading
    B back must not surface A's name: the swap decrypts to garbage (fail
    closed) rather than handing B the attacker's plaintext."""
    attacker_name = f"attacker-name-{uuid.uuid4().hex}"
    victim_name = f"victim-name-{uuid.uuid4().hex}"

    a = auth_client.post("/v1/members", json={"name": attacker_name}).json()
    b = auth_client.post("/v1/members", json={"name": victim_name}).json()

    assert _run_sql(
        "UPDATE members SET name = (SELECT name FROM members WHERE id = :src) "
        "WHERE id = :dst",
        src=a["id"],
        dst=b["id"],
    ) == 1
    # Prove the swap landed: B's stored cell now holds A's ciphertext.
    assert _fetch_one(
        "SELECT name FROM members WHERE id = :id", id=b["id"]
    ) == _fetch_one("SELECT name FROM members WHERE id = :id", id=a["id"])

    resp = auth_client.get(f"/v1/members/{b['id']}")
    # Fail-closed (decrypt raises -> 5xx) or a placeholder both satisfy the
    # guarantee: A's plaintext must not appear as B's name.
    assert attacker_name not in resp.text, resp.text
    if resp.status_code == 200:
        assert resp.json()["name"] != attacker_name


# ---------------------------------------------------------------------------
# 2. Cross-row swap (journal_entries.body)
# ---------------------------------------------------------------------------


def test_journal_body_cross_row_swap_fails_closed(auth_client: httpx.Client):
    """Attacker copies journal entry A's `body` ciphertext onto entry B.
    The body ciphertext is bound to A's entry pk, so reading B back must
    not return A's body text."""
    attacker_body = f"attacker-body-{uuid.uuid4().hex}"
    victim_body = f"victim-body-{uuid.uuid4().hex}"

    a = auth_client.post("/v1/journals", json={"body": attacker_body}).json()
    b = auth_client.post("/v1/journals", json={"body": victim_body}).json()

    assert _run_sql(
        "UPDATE journal_entries SET body = "
        "(SELECT body FROM journal_entries WHERE id = :src) WHERE id = :dst",
        src=a["id"],
        dst=b["id"],
    ) == 1
    # Prove the swap landed: B's stored cell now holds A's ciphertext.
    assert _fetch_one(
        "SELECT body FROM journal_entries WHERE id = :id", id=b["id"]
    ) == _fetch_one(
        "SELECT body FROM journal_entries WHERE id = :id", id=a["id"]
    )

    resp = auth_client.get(f"/v1/journals/{b['id']}")
    assert attacker_body not in resp.text, resp.text
    if resp.status_code == 200:
        assert resp.json()["body"] != attacker_body


# ---------------------------------------------------------------------------
# 3. TOTP-secret relocation is not an auth bypass (users.totp_secret)
# ---------------------------------------------------------------------------


def test_totp_secret_relocation_is_not_auth_bypass():
    """The scenario the whole feature exists to close: an attacker with DB
    write access lifts user A's enrolled `totp_secret` ciphertext onto
    victim user B's row and flips B's `totp_enabled` flag, hoping to log in
    as B using codes from a factor the attacker controls.

    A's secret is bound to A's user pk, so when login decrypts B's
    totp_secret under B's own AAD it fails closed instead of yielding A's
    shared secret. A login as B carrying a currently-valid code from A's
    factor must be rejected."""
    with (
        httpx.Client(base_url=BASE_URL) as attacker_c,
        httpx.Client(base_url=BASE_URL) as victim_c,
    ):
        attacker_email = f"aad-totp-a-{uuid.uuid4().hex[:8]}@sheaf.dev"
        victim_email = f"aad-totp-b-{uuid.uuid4().hex[:8]}@sheaf.dev"
        attacker_id = _register(attacker_c, attacker_email)
        victim_id = _register(victim_c, victim_email)

        # Attacker enrols TOTP on their own account the normal way, which
        # stores the secret as a v2 ciphertext bound to the attacker's pk.
        setup = attacker_c.post(
            "/v1/auth/totp/setup", json={"password": PASSWORD}
        )
        assert setup.status_code == 200, setup.text
        attacker_totp = pyotp.TOTP(setup.json()["secret"])
        verify = attacker_c.post(
            "/v1/auth/totp/verify", json={"code": attacker_totp.now()}
        )
        assert verify.status_code == 204, verify.text

        # DB-write attacker: relocate the secret onto the victim and enable
        # the factor on the victim's account.
        assert _run_sql(
            "UPDATE users SET totp_secret = "
            "(SELECT totp_secret FROM users WHERE id = :src), "
            "totp_enabled = TRUE WHERE id = :dst",
            src=attacker_id,
            dst=victim_id,
        ) == 1
        # Prove the relocation landed before asserting login refuses it.
        assert _fetch_one(
            "SELECT totp_secret FROM users WHERE id = :id", id=victim_id
        ) == _fetch_one(
            "SELECT totp_secret FROM users WHERE id = :id", id=attacker_id
        )

        # Log in as the victim with a fresh, valid code from the attacker's
        # factor. If the binding held, decrypt of the victim's totp_secret
        # fails and login cannot succeed.
        resp = victim_c.post(
            "/v1/auth/login",
            json={
                "email": victim_email,
                "password": PASSWORD,
                "totp_code": attacker_totp.now(),
            },
        )
        assert resp.status_code != 200, resp.text
        assert "access_token" not in resp.text, resp.text


# ---------------------------------------------------------------------------
# 4. Legacy v1 rows still read (dual-read regression)
# ---------------------------------------------------------------------------


def test_legacy_v1_member_name_still_reads(auth_client: httpx.Client):
    """Rows written before the v2 conversion carry unprefixed v1
    ciphertexts with no AAD. The dual-read path must still decrypt them, or
    converting a call site to v2 would orphan every pre-conversion row.

    Simulate an old row by overwriting the name with a v1 token produced by
    `encrypt(plaintext)` (no aad, no prefix), then read it back through the
    API and confirm the plaintext returns intact."""
    from sheaf.crypto import encrypt

    legacy_name = f"legacy-v1-name-{uuid.uuid4().hex}"
    member = auth_client.post(
        "/v1/members", json={"name": f"placeholder-{uuid.uuid4().hex}"}
    ).json()

    # v1: unprefixed SecretBox token, exactly what old rows hold.
    v1_token = encrypt(legacy_name)
    assert not v1_token.startswith("v2:")
    assert _run_sql(
        "UPDATE members SET name = :ct WHERE id = :id",
        ct=v1_token,
        id=member["id"],
    ) == 1
    assert _fetch_one(
        "SELECT name FROM members WHERE id = :id", id=member["id"]
    ) == v1_token

    resp = auth_client.get(f"/v1/members/{member['id']}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == legacy_name


# ---------------------------------------------------------------------------
# 5. Wrong-column swap (members.note -> members.description, same row)
# ---------------------------------------------------------------------------


def test_member_note_into_description_fails_closed(auth_client: httpx.Client):
    """Same row, different column: attacker copies a member's `note`
    ciphertext into that member's `description` cell. The AAD binds each
    ciphertext to its column, so the note text must not resurface as the
    description even though the row pk is unchanged."""
    secret_note = f"secret-note-{uuid.uuid4().hex}"
    member = auth_client.post(
        "/v1/members",
        json={
            "name": f"note-holder-{uuid.uuid4().hex[:8]}",
            "description": "an ordinary bio",
            "note": secret_note,
        },
    ).json()

    assert _run_sql(
        "UPDATE members SET description = note WHERE id = :id",
        id=member["id"],
    ) == 1
    # Prove the swap landed: the description cell now holds the note's
    # ciphertext.
    assert _fetch_one(
        "SELECT description FROM members WHERE id = :id", id=member["id"]
    ) == _fetch_one("SELECT note FROM members WHERE id = :id", id=member["id"])

    resp = auth_client.get(f"/v1/members/{member['id']}")
    assert secret_note not in resp.text, resp.text
    if resp.status_code == 200:
        assert resp.json()["description"] != secret_note


# ---------------------------------------------------------------------------
# 6. Webhook-secret relocation must not sign deliveries
#    (notification_channels.webhook_secret_encrypted)
# ---------------------------------------------------------------------------


def _create_webhook_channel(
    client: httpx.Client, *, name: str, secret: str
) -> str:
    """Create a webhook channel with a signing secret via the API; returns
    the channel id. Mirrors the helper in test_notifications_api.py."""
    sid = client.get("/v1/systems/me").json()["id"]
    tok = client.post(
        f"/v1/systems/{sid}/watch-tokens", json={"label": name}
    ).json()
    resp = client.post(
        f"/v1/watch-tokens/{tok['id']}/channels",
        json={
            "name": name,
            "destination_type": "webhook",
            "destination_config": {"url": "https://example.com/webhook"},
            "webhook_secret": secret,
            "base_all_members": True,
            "trigger_on_start": True,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["channel"]["id"]


def test_webhook_secret_relocation_fails_closed(
    auth_client: httpx.Client, monkeypatch
):
    """Attacker copies channel B's `webhook_secret_encrypted` ciphertext onto
    channel A at the DB layer, hoping A's next delivery gets signed with a
    secret of the attacker's choosing. B's secret is bound to B's channel pk,
    so the delivery-side decrypt on A fails closed and the delivery is
    dropped (permanent failure) before any signature is produced.

    Drives `_deliver_webhook` directly with a DB-loaded channel (the same
    in-process pattern as test_notifications_mobile_dispatch.py), with DNS
    resolution stubbed and the HTTP client replaced by a sentinel that fails
    the test if delivery ever reaches the network."""
    from sheaf.services.notifications.safe_http import PinnedRequest

    a_id = _create_webhook_channel(
        auth_client, name="victim", secret="victim-secret"
    )
    b_id = _create_webhook_channel(
        auth_client, name="donor", secret="donor-secret"
    )

    # DB-write attacker: relocate B's secret ciphertext onto A.
    assert _run_sql(
        "UPDATE notification_channels SET webhook_secret_encrypted = "
        "(SELECT webhook_secret_encrypted FROM notification_channels "
        "WHERE id = :src) WHERE id = :dst",
        src=b_id,
        dst=a_id,
    ) == 1
    assert _fetch_one(
        "SELECT webhook_secret_encrypted FROM notification_channels "
        "WHERE id = :id",
        id=a_id,
    ) == _fetch_one(
        "SELECT webhook_secret_encrypted FROM notification_channels "
        "WHERE id = :id",
        id=b_id,
    )

    async def fake_resolve(url: str) -> PinnedRequest:
        return PinnedRequest(url=url)

    class _NoHttp:
        async def __aenter__(self):
            raise AssertionError(
                "delivery reached the HTTP client: the relocated secret was "
                "used instead of failing closed"
            )

        async def __aexit__(self, *exc) -> bool:
            return False

    monkeypatch.setattr(
        "sheaf.services.notifications.handlers.resolve_pinned", fake_resolve
    )
    monkeypatch.setattr(
        "sheaf.services.notifications.handlers.safe_client",
        lambda timeout=10.0: _NoHttp(),
    )

    async def run():
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import (
            AsyncSession,
            create_async_engine,
        )
        from sqlalchemy.orm import sessionmaker

        from sheaf.models.notification_channel import NotificationChannel
        from sheaf.services.notifications.handlers import _deliver_webhook
        from sheaf.services.notifications.payload import RenderedMessage

        engine = _test_engine()
        async_session = sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        try:
            async with async_session() as db:
                channel = (
                    await db.execute(
                        select(NotificationChannel).where(
                            NotificationChannel.id == uuid.UUID(a_id)
                        )
                    )
                ).scalar_one()
                return await _deliver_webhook(
                    channel,
                    RenderedMessage(title="hello", body="world"),
                    event_id=str(uuid.uuid4()),
                )
        finally:
            await engine.dispose()

    result = asyncio.run(run())
    assert result.ok is False
    assert result.permanent, result
    assert "secret decryption failed" in (result.error or ""), result
