import os
import uuid

import httpx
import pyotp
import pytest


def test_register(client: httpx.Client):
    email = f"reg-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "securepassword"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_register_duplicate_email(client: httpx.Client):
    email = f"dupe-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/register", json={"email": email, "password": "otherpassword"})
    assert resp.status_code == 409


def test_register_short_password(client: httpx.Client):
    resp = client.post(
        "/v1/auth/register",
        json={"email": "short@sheaf.dev", "password": "abc"},
    )
    assert resp.status_code == 422


def test_login(client: httpx.Client):
    email = f"login-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/login", json={"email": email, "password": "securepassword"})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_login_wrong_password(client: httpx.Client):
    email = f"wrong-{uuid.uuid4().hex[:8]}@sheaf.dev"
    client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    resp = client.post("/v1/auth/login", json={"email": email, "password": "wrongpassword"})
    assert resp.status_code == 401


def test_me(auth_client: httpx.Client):
    resp = auth_client.get("/v1/auth/me")
    assert resp.status_code == 200
    data = resp.json()
    assert "@sheaf.dev" in data["email"]
    assert data["totp_enabled"] is False


def test_unauthenticated(client: httpx.Client):
    resp = client.get("/v1/systems/me")
    assert resp.status_code in (401, 403)


def test_refresh_token(client: httpx.Client):
    email = f"refresh-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    refresh_token = resp.json()["refresh_token"]
    resp = client.post("/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert resp.status_code == 200
    assert "access_token" in resp.json()


def test_refresh_token_via_cookie(client: httpx.Client):
    """Frontend pattern: POST /refresh with empty body, refresh JWT comes from
    the HttpOnly cookie set on register/login. Browsers will silently drop a
    Secure cookie sent over HTTP, so we also assert the cookie's Secure flag
    matches the server's base URL scheme — otherwise dev-over-HTTP refresh
    breaks the moment the access token expires."""
    email = f"refresh-cookie-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    assert resp.status_code == 201
    refresh_header = next(
        (h for h in resp.headers.get_list("set-cookie") if h.startswith("sheaf_refresh=")),
        "",
    )
    assert refresh_header, "register must set sheaf_refresh cookie"
    base_url = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")
    if base_url.startswith("http://"):
        assert "Secure" not in refresh_header, (
            "cookie must NOT be Secure when serving over HTTP — "
            "browsers drop it and refresh silently breaks"
        )
    else:
        assert "Secure" in refresh_header

    assert client.cookies.get("sheaf_refresh"), "client should have stored sheaf_refresh"
    cookie_resp = client.post("/v1/auth/refresh", json={})
    assert cookie_resp.status_code == 200, cookie_resp.text
    assert "access_token" in cookie_resp.json()


def test_refresh_concurrent_replay_does_not_kill_session(client: httpx.Client):
    """Two callers race to /refresh with the same refresh token (StrictMode
    double-fire, parallel queries on page load, two tabs reloading at once).
    Only one wins the GETDEL; the other historically tripped reuse-detection
    and the session was nuked. The grace-window cache lets the loser replay
    the same rotation result instead.
    """
    email = f"refresh-replay-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post("/v1/auth/register", json={"email": email, "password": "securepassword"})
    assert resp.status_code == 201
    original_refresh_jwt = resp.json()["refresh_token"]

    # Winner consumes the jti and gets a new refresh.
    r1 = client.post("/v1/auth/refresh", json={"refresh_token": original_refresh_jwt})
    assert r1.status_code == 200, r1.text

    # Loser arrives with the *same* original token — within the grace window
    # this should replay rather than trigger session-kill.
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": original_refresh_jwt})
    assert r2.status_code == 200, (
        f"Concurrent /refresh duplicate should replay within the grace window, "
        f"got {r2.status_code}: {r2.text}"
    )

    # Session must still be usable: hit /me with the winner's access token.
    access = r1.json()["access_token"]
    me = client.get("/v1/auth/me", headers={"Authorization": f"Bearer {access}"})
    assert me.status_code == 200, me.text


def test_secondary_session_mints_independent_session(client: httpx.Client):
    """A primary device (phone) can mint a child session for a paired
    companion (watch). Both sessions exist concurrently in /sessions and
    each holds its own one-shot refresh token, so the two devices can
    rotate independently without colliding through the GETDEL serialisation
    that previously made shared tokens fragile across the WCSession sync."""
    email = f"secondary-mint-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    assert resp.status_code == 201, resp.text
    primary_access = resp.json()["access_token"]
    primary_refresh = resp.json()["refresh_token"]

    secondary_resp = client.post(
        "/v1/auth/sessions/secondary",
        headers={
            "Authorization": f"Bearer {primary_access}",
            "X-Sheaf-Client": "Sheaf watchOS/1.0",
        },
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    assert secondary_resp.status_code == 201, secondary_resp.text
    body = secondary_resp.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["session_id"]
    watch_refresh = body["refresh_token"]

    # Both sessions show up in /sessions, distinct ids.
    list_resp = client.get(
        "/v1/auth/sessions",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert list_resp.status_code == 200, list_resp.text
    ids = {s["id"] for s in list_resp.json()}
    assert body["session_id"] in ids
    assert len(ids) >= 2

    # Watch's refresh works — independent jti, doesn't touch primary's.
    r_watch = client.post(
        "/v1/auth/refresh", json={"refresh_token": watch_refresh},
    )
    assert r_watch.status_code == 200, r_watch.text

    # Primary's refresh ALSO still works — was never consumed by the watch.
    r_phone = client.post(
        "/v1/auth/refresh", json={"refresh_token": primary_refresh},
    )
    assert r_phone.status_code == 200, r_phone.text


def test_secondary_session_cascade_on_parent_logout(client: httpx.Client):
    """Revoking the parent session must take its child sessions with it.
    Cascade lives server-side so it fires whether the user logs out from
    the phone, deletes the phone session from a browser, or anywhere else
    — the phone doesn't have to remember the watch sid to do the cleanup
    itself."""
    email = f"secondary-cascade-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    secondary_resp = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {primary_access}"},
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    assert secondary_resp.status_code == 201, secondary_resp.text
    watch_access = secondary_resp.json()["access_token"]
    watch_refresh = secondary_resp.json()["refresh_token"]

    # Watch can hit /me before the cascade.
    me = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
    )
    assert me.status_code == 200

    # Logging out the phone should cascade-kill the watch's session too.
    logout = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert logout.status_code == 204

    # Watch access token is now bound to a dead session.
    me_after = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
    )
    assert me_after.status_code == 401

    # And the watch's refresh token can't resurrect the session either.
    refresh_after = client.post(
        "/v1/auth/refresh", json={"refresh_token": watch_refresh},
    )
    assert refresh_after.status_code == 401


def test_secondary_session_can_be_revoked_alone(client: httpx.Client):
    """Killing only the watch session must leave the phone alive — the user
    revoking a wearable from /sessions shouldn't get logged out of the
    primary device."""
    email = f"secondary-solo-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    secondary_resp = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {primary_access}"},
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    watch_sid = secondary_resp.json()["session_id"]
    watch_access = secondary_resp.json()["access_token"]

    revoke = client.delete(
        f"/v1/auth/sessions/{watch_sid}",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert revoke.status_code == 204, revoke.text

    # Watch is dead.
    me_watch = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
    )
    assert me_watch.status_code == 401

    # Phone is still alive.
    me_phone = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert me_phone.status_code == 200


def test_secondary_session_change_password_keeps_paired_watch(
    client: httpx.Client,
):
    """change-password runs delete_other_sessions, which would naively kill
    the watch (it's "another session"). The whole point of the parent/child
    link is that the watch is a *companion* of the calling phone — keep it
    alive across the calling session's password change so the user doesn't
    have to re-pair the watch every time they rotate their password."""
    email = f"secondary-chpw-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "originalpass1"},
    )
    primary_access = resp.json()["access_token"]

    secondary_resp = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {primary_access}"},
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    assert secondary_resp.status_code == 201, secondary_resp.text
    watch_access = secondary_resp.json()["access_token"]

    # Need the session cookie for change-password to identify "this session".
    login = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "originalpass1"},
    )
    assert login.status_code == 200
    login_access = login.json()["access_token"]

    # Mint a watch session bound to the *cookie* session (which is the one
    # that will run change-password).
    secondary_via_cookie = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {login_access}"},
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    assert secondary_via_cookie.status_code == 201
    cookie_watch_access = secondary_via_cookie.json()["access_token"]

    chpw = client.post(
        "/v1/auth/change-password",
        headers={"Authorization": f"Bearer {login_access}"},
        json={
            "current_password": "originalpass1",
            "new_password": "freshpass2",
        },
    )
    assert chpw.status_code == 200, chpw.text

    # Watch paired with the calling session survives.
    me_keep = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {cookie_watch_access}"},
    )
    assert me_keep.status_code == 200, me_keep.text

    # Watch paired with the *other* (now-revoked) session does not.
    me_gone = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
    )
    assert me_gone.status_code == 401


def _mint_secondary(
    client: httpx.Client, parent_access: str, label: str = "Sheaf watchOS/1.0",
) -> dict:
    """Helper: ask the server for a child session under `parent_access` and
    return the parsed response body."""
    resp = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {parent_access}"},
        json={"client_name": label},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_secondary_session_cascade_on_sessions_delete(client: httpx.Client):
    """The /sessions/{id} DELETE path must cascade just like /logout. A user
    revoking their phone session from the web `/sessions` UI is a different
    code path than calling /logout on the phone, but the user expectation
    is the same: the watch tied to it goes too. Without coverage here a
    cascade regression in only one of the two paths could ship unnoticed."""
    email = f"secondary-sessions-del-{uuid.uuid4().hex[:8]}@sheaf.dev"
    # Register-only (single primary session) so we have an unambiguous
    # parent to address. _register_and_login would create two.
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    assert reg.status_code == 201, reg.text
    primary_access = reg.json()["access_token"]

    # Use a *second* session (distinct phone-ish session) to revoke the
    # first one's watch — mirroring the "kick this device off from somewhere
    # else" UX. Log in to get an unrelated session.
    fresh = httpx.Client(base_url=client.base_url)
    try:
        login = fresh.post(
            "/v1/auth/login", json={"email": email, "password": "securepassword"},
        )
        assert login.status_code == 200
        outsider_access = login.json()["access_token"]

        # Mint the watch session from the *original* primary so we have a
        # known parent-child pair.
        watch = _mint_secondary(client, primary_access)
        watch_access = watch["access_token"]

        # Find the primary by elimination: list all sessions, drop the
        # watch (known sid) and the outsider (cookie-marked is_current on
        # `fresh`).
        sessions = client.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {primary_access}"},
        ).json()
        outsider_sessions = fresh.get(
            "/v1/auth/sessions",
            headers={"Authorization": f"Bearer {outsider_access}"},
        ).json()
        outsider_ids = {s["id"] for s in outsider_sessions if s["is_current"]}
        primary_sid_candidates = [
            s["id"]
            for s in sessions
            if s["id"] != watch["session_id"] and s["id"] not in outsider_ids
        ]
        assert len(primary_sid_candidates) == 1, primary_sid_candidates
        primary_sid = primary_sid_candidates[0]

        # Revoke the parent from the outsider session.
        revoke = fresh.delete(
            f"/v1/auth/sessions/{primary_sid}",
            headers={"Authorization": f"Bearer {outsider_access}"},
        )
        assert revoke.status_code == 204, revoke.text

        # Watch dies along with the parent.
        me_watch = client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
        )
        assert me_watch.status_code == 401

        # Outsider session is unaffected.
        me_outsider = fresh.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {outsider_access}"},
        )
        assert me_outsider.status_code == 200
    finally:
        fresh.close()


def test_secondary_session_revoke_others_cascade_semantics(client: httpx.Client):
    """The /sessions/revoke-others sweep must:
      - keep the calling session AND its paired watch alive, and
      - cascade-revoke the watch of any *other* session it kicks off.
    Both halves matter: revoking a second laptop's session on a shared
    account shouldn't leave that laptop's wearable still authorised."""
    email = f"secondary-revoke-others-{uuid.uuid4().hex[:8]}@sheaf.dev"
    _register_and_login(client, email, "securepassword")

    # Phone A and its watch.
    phone_a = client.post(
        "/v1/auth/login", json={"email": email, "password": "securepassword"},
    )
    assert phone_a.status_code == 200
    phone_a_access = phone_a.json()["access_token"]
    watch_a = _mint_secondary(client, phone_a_access, label="Watch A")
    watch_a_access = watch_a["access_token"]

    # Phone B and its watch (separate browser-equivalent client so the
    # cookie session is independent).
    other = httpx.Client(base_url=client.base_url)
    try:
        phone_b = other.post(
            "/v1/auth/login", json={"email": email, "password": "securepassword"},
        )
        assert phone_b.status_code == 200
        phone_b_access = phone_b.json()["access_token"]
        watch_b = _mint_secondary(other, phone_b_access, label="Watch B")
        watch_b_access = watch_b["access_token"]

        # Phone A revokes everything else.
        revoke = client.post(
            "/v1/auth/sessions/revoke-others",
            headers={"Authorization": f"Bearer {phone_a_access}"},
        )
        assert revoke.status_code == 200, revoke.text

        # Watch A is paired with the kept session — stays alive.
        me_a = client.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {watch_a_access}"},
        )
        assert me_a.status_code == 200, me_a.text

        # Phone B's session is gone, and so is its watch.
        me_b_phone = other.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {phone_b_access}"},
        )
        assert me_b_phone.status_code == 401
        me_b_watch = other.get(
            "/v1/auth/me", headers={"Authorization": f"Bearer {watch_b_access}"},
        )
        assert me_b_watch.status_code == 401, (
            "Phone B's watch must die with phone B — revoke-others has to "
            "cascade through to children of revoked siblings, not just "
            "delete the parent rows."
        )
    finally:
        other.close()


def test_secondary_session_grandchild_cascade(client: httpx.Client):
    """Cascade must traverse the full chain. A child minting its own child
    (e.g. a hypothetical companion of a companion) must be killed when the
    root session is revoked. Today nothing in the product mints
    grandchildren, but the cascade contract has to hold or future features
    layered on this primitive will silently leak sessions."""
    email = f"secondary-grandchild-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    child = _mint_secondary(client, primary_access, label="Child")
    grandchild = _mint_secondary(
        client, child["access_token"], label="Grandchild",
    )
    grandchild_access = grandchild["access_token"]

    logout = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert logout.status_code == 204

    me = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {grandchild_access}"},
    )
    assert me.status_code == 401, (
        "Grandchild must be cascade-revoked when the root parent is "
        "logged out, not just direct children."
    )


def test_secondary_session_multiple_children_all_cascade(client: httpx.Client):
    """A single phone session might back several wearables (watch + future
    companions) — the cascade has to reach all of them, not just the first
    one Redis happens to return from SMEMBERS."""
    email = f"secondary-multi-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    children = [
        _mint_secondary(client, primary_access, label=f"Companion {i}")
        for i in range(3)
    ]

    # All three must respond before the cascade.
    for child in children:
        me = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {child['access_token']}"},
        )
        assert me.status_code == 200

    logout = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert logout.status_code == 204

    # And none of them after.
    for child in children:
        me = client.get(
            "/v1/auth/me",
            headers={"Authorization": f"Bearer {child['access_token']}"},
        )
        assert me.status_code == 401, (
            f"Child {child['session_id']} survived the cascade — "
            f"multi-child revocation is broken."
        )


def test_secondary_session_concurrent_refresh_replay_window(
    client: httpx.Client,
):
    """The replay-window grace that protects the phone's refresh from
    concurrent racing has to apply to the watch's refresh too. The watch
    can fire parallel /refresh calls (a complication update racing the
    main app's request retry) and the second one must replay rather than
    nuke the session — otherwise the very fix we shipped for the phone is
    only half-built."""
    email = f"secondary-watch-race-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    watch = _mint_secondary(client, primary_access)
    watch_refresh = watch["refresh_token"]
    watch_access = watch["access_token"]

    # Two callers race the same watch refresh token.
    r1 = client.post("/v1/auth/refresh", json={"refresh_token": watch_refresh})
    assert r1.status_code == 200, r1.text
    r2 = client.post("/v1/auth/refresh", json={"refresh_token": watch_refresh})
    assert r2.status_code == 200, (
        f"Concurrent /refresh on the watch's token should replay within "
        f"the grace window, got {r2.status_code}: {r2.text}"
    )

    # Watch session is still alive.
    me = client.get(
        "/v1/auth/me", headers={"Authorization": f"Bearer {watch_access}"},
    )
    assert me.status_code == 200, me.text


def test_secondary_session_mint_after_parent_revoked_fails(
    client: httpx.Client,
):
    """If the calling session was deleted out-of-band between the access
    token being minted and the secondary-session call landing, the mint
    must fail. Otherwise an attacker with a stolen access-token-but-dead-
    session could spawn a fresh refresh JWT bound to a child session and
    keep going indefinitely after the user thought they'd logged out
    everywhere."""
    email = f"secondary-after-revoke-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register", json={"email": email, "password": "securepassword"},
    )
    primary_access = resp.json()["access_token"]

    logout = client.post(
        "/v1/auth/logout",
        headers={"Authorization": f"Bearer {primary_access}"},
    )
    assert logout.status_code == 204

    # The access token JWT itself is still cryptographically valid until
    # it expires, but its session is gone. The dependency layer enforces
    # this — the secondary endpoint shouldn't be reachable.
    mint = client.post(
        "/v1/auth/sessions/secondary",
        headers={"Authorization": f"Bearer {primary_access}"},
        json={"client_name": "Sheaf watchOS/1.0"},
    )
    assert mint.status_code == 401, mint.text


@pytest.mark.selfhosted
def test_registration_defaults_to_self_hosted_tier(client: httpx.Client):
    """Self-hosted instance: new signups land on the self_hosted tier
    (unlimited), matching the model default. SaaS mode is covered by
    test_registration_defaults_to_free_tier_in_saas."""
    email = f"tier-sh-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert reg.status_code == 201, reg.text
    me = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {reg.json()['access_token']}"},
    )
    assert me.json()["tier"] == "self_hosted"


@pytest.mark.saas
def test_registration_defaults_to_free_tier_in_saas(client: httpx.Client):
    """SaaS instance: new signups must start on the free tier so tier limits
    (member count, storage quota) actually apply. Admins bump individuals up
    out of band."""
    email = f"tier-saas-{uuid.uuid4().hex[:8]}@sheaf.dev"
    reg = client.post(
        "/v1/auth/register", json={"email": email, "password": "testpassword123"}
    )
    assert reg.status_code == 201, reg.text
    me = client.get(
        "/v1/auth/me",
        headers={"Authorization": f"Bearer {reg.json()['access_token']}"},
    )
    assert me.json()["tier"] == "free"


def _register_and_login(client: httpx.Client, email: str, password: str) -> str:
    """Register a user and log in via cookie session. Returns access token."""
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def test_change_password_success(client: httpx.Client):
    email = f"chpw-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "oldpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "oldpassword123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["changed"] is True

    # Old password no longer logs in.
    r = client.post("/v1/auth/login", json={"email": email, "password": "oldpassword123"})
    assert r.status_code == 401
    # New password does.
    r = client.post("/v1/auth/login", json={"email": email, "password": "newpassword456"})
    assert r.status_code == 200


def test_change_password_wrong_current(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401


def test_change_password_same_as_current(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "testpassword123", "new_password": "testpassword123"},
    )
    assert resp.status_code == 400


def test_change_password_too_short(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-password",
        json={"current_password": "testpassword123", "new_password": "short"},
    )
    assert resp.status_code == 400


def test_change_password_unauthenticated(client: httpx.Client):
    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "x", "new_password": "newpassword456"},
    )
    assert resp.status_code in (401, 403)


def test_change_password_with_totp(client: httpx.Client):
    email = f"chpw-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "oldpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    setup = client.post("/v1/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    totp = pyotp.TOTP(secret)
    verify = client.post("/v1/auth/totp/verify", json={"code": totp.now()})
    assert verify.status_code == 204, verify.text

    # Missing TOTP -> 401 with X-Sheaf-2FA header.
    resp = client.post(
        "/v1/auth/change-password",
        json={"current_password": "oldpassword123", "new_password": "newpassword456"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("X-Sheaf-2FA") == "required"

    # Wrong TOTP -> 401, no header (the gate is past).
    resp = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "oldpassword123",
            "new_password": "newpassword456",
            "totp_code": "000000",
        },
    )
    assert resp.status_code == 401

    # Correct TOTP -> success.
    resp = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": "oldpassword123",
            "new_password": "newpassword456",
            "totp_code": totp.now(),
        },
    )
    assert resp.status_code == 200, resp.text


def test_change_email_success(client: httpx.Client):
    email = f"chem-{uuid.uuid4().hex[:8]}@sheaf.dev"
    new_email = f"chem-new-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "testpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "testpassword123"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == new_email

    # Old email no longer logs in.
    r = client.post("/v1/auth/login", json={"email": email, "password": "testpassword123"})
    assert r.status_code == 401
    # New email does.
    r = client.post(
        "/v1/auth/login", json={"email": new_email, "password": "testpassword123"},
    )
    assert r.status_code == 200


def test_change_email_wrong_password(auth_client: httpx.Client):
    new_email = f"chem-bad-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "wrong"},
    )
    assert resp.status_code == 401


def test_change_email_same_as_current(auth_client: httpx.Client):
    me = auth_client.get("/v1/auth/me").json()
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": me["email"], "current_password": "testpassword123"},
    )
    assert resp.status_code == 400


def test_change_email_invalid_format(auth_client: httpx.Client):
    resp = auth_client.post(
        "/v1/auth/change-email",
        json={"new_email": "not-an-email", "current_password": "testpassword123"},
    )
    assert resp.status_code == 422


def test_change_email_conflict(client: httpx.Client):
    a = f"chem-a-{uuid.uuid4().hex[:8]}@sheaf.dev"
    b = f"chem-b-{uuid.uuid4().hex[:8]}@sheaf.dev"
    # Register both users.
    client.post("/v1/auth/register", json={"email": a, "password": "testpassword123"})
    token_b = _register_and_login(client, b, "testpassword123")

    # User B tries to take user A's email.
    client.headers["Authorization"] = f"Bearer {token_b}"
    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": a, "current_password": "testpassword123"},
    )
    assert resp.status_code == 409


def test_change_email_with_totp(client: httpx.Client):
    email = f"chem-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    new_email = f"chem-totp-new-{uuid.uuid4().hex[:8]}@sheaf.dev"
    token = _register_and_login(client, email, "testpassword123")
    client.headers["Authorization"] = f"Bearer {token}"

    setup = client.post("/v1/auth/totp/setup")
    secret = setup.json()["secret"]
    totp = pyotp.TOTP(secret)
    client.post("/v1/auth/totp/verify", json={"code": totp.now()})

    # Missing TOTP -> 401 with X-Sheaf-2FA header.
    resp = client.post(
        "/v1/auth/change-email",
        json={"new_email": new_email, "current_password": "testpassword123"},
    )
    assert resp.status_code == 401
    assert resp.headers.get("X-Sheaf-2FA") == "required"

    # Correct TOTP -> success.
    resp = client.post(
        "/v1/auth/change-email",
        json={
            "new_email": new_email,
            "current_password": "testpassword123",
            "totp_code": totp.now(),
        },
    )
    assert resp.status_code == 200


def _enrol_totp(client: httpx.Client) -> "pyotp.TOTP":
    setup = client.post("/v1/auth/totp/setup")
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    totp = pyotp.TOTP(secret)
    verify = client.post("/v1/auth/totp/verify", json={"code": totp.now()})
    assert verify.status_code == 204, verify.text
    return totp


def test_remember_device_skips_totp_on_next_login(client: httpx.Client):
    email = f"rd-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    token = _register_and_login(client, email, password)
    client.headers["Authorization"] = f"Bearer {token}"
    totp = _enrol_totp(client)

    # First login with remember_device=True: must include a TOTP code, and
    # the response sets the trusted-device cookie.
    r = client.post(
        "/v1/auth/login",
        json={
            "email": email,
            "password": password,
            "totp_code": totp.now(),
            "remember_device": True,
        },
    )
    assert r.status_code == 200, r.text
    trusted_cookie = r.cookies.get("sheaf_trusted_device")
    assert trusted_cookie

    # Second login without TOTP, but presenting the trusted-device cookie
    # — should succeed.
    r = client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        cookies={"sheaf_trusted_device": trusted_cookie},
    )
    assert r.status_code == 200, r.text


def test_remember_device_requires_totp_first(client: httpx.Client):
    email = f"rd-need-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    token = _register_and_login(client, email, password)
    client.headers["Authorization"] = f"Bearer {token}"
    _enrol_totp(client)

    # remember_device=True without a TOTP code on the first login still
    # rejects with the 2FA-required signal.
    del client.headers["Authorization"]
    r = client.post(
        "/v1/auth/login",
        json={"email": email, "password": password, "remember_device": True},
    )
    assert r.status_code == 401
    assert r.headers.get("X-Sheaf-2FA") == "required"


def test_trusted_device_bound_to_user(client: httpx.Client):
    """A cookie minted for user A must not let user B skip TOTP."""
    pw = "testpassword123"
    email_a = f"rd-bind-a-{uuid.uuid4().hex[:8]}@sheaf.dev"
    email_b = f"rd-bind-b-{uuid.uuid4().hex[:8]}@sheaf.dev"

    # User A: enrol TOTP and mint a trusted-device cookie.
    token_a = _register_and_login(client, email_a, pw)
    client.headers["Authorization"] = f"Bearer {token_a}"
    totp_a = _enrol_totp(client)
    r = client.post(
        "/v1/auth/login",
        json={
            "email": email_a, "password": pw,
            "totp_code": totp_a.now(), "remember_device": True,
        },
    )
    cookie_a = r.cookies.get("sheaf_trusted_device")
    assert cookie_a

    # User B: enrol TOTP, then try to log in presenting A's cookie.
    del client.headers["Authorization"]
    token_b = _register_and_login(client, email_b, pw)
    client.headers["Authorization"] = f"Bearer {token_b}"
    _enrol_totp(client)
    del client.headers["Authorization"]

    r = client.post(
        "/v1/auth/login",
        json={"email": email_b, "password": pw},
        cookies={"sheaf_trusted_device": cookie_a},
    )
    # B has TOTP enabled and didn't supply a code; A's cookie shouldn't
    # bypass for B.
    assert r.status_code == 401
    assert r.headers.get("X-Sheaf-2FA") == "required"


def test_change_password_revokes_trusted_devices(client: httpx.Client):
    email = f"rd-pw-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    token = _register_and_login(client, email, password)
    client.headers["Authorization"] = f"Bearer {token}"
    totp = _enrol_totp(client)
    r = client.post(
        "/v1/auth/login",
        json={
            "email": email, "password": password,
            "totp_code": totp.now(), "remember_device": True,
        },
    )
    cookie = r.cookies.get("sheaf_trusted_device")
    assert cookie

    # Change password.
    r = client.post(
        "/v1/auth/change-password",
        json={
            "current_password": password, "new_password": "newpassword456",
            "totp_code": totp.now(),
        },
    )
    assert r.status_code == 200, r.text

    # Old cookie no longer bypasses TOTP — the row was wiped.
    del client.headers["Authorization"]
    r = client.post(
        "/v1/auth/login",
        json={"email": email, "password": "newpassword456"},
        cookies={"sheaf_trusted_device": cookie},
    )
    assert r.status_code == 401
    assert r.headers.get("X-Sheaf-2FA") == "required"


def test_totp_disable_revokes_trusted_devices(client: httpx.Client):
    email = f"rd-totp-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    token = _register_and_login(client, email, password)
    client.headers["Authorization"] = f"Bearer {token}"
    totp = _enrol_totp(client)
    r = client.post(
        "/v1/auth/login",
        json={
            "email": email, "password": password,
            "totp_code": totp.now(), "remember_device": True,
        },
    )
    cookie = r.cookies.get("sheaf_trusted_device")
    assert cookie

    # Disable TOTP.
    r = client.post(
        "/v1/auth/totp/disable",
        json={"email": email, "password": password, "totp_code": totp.now()},
    )
    assert r.status_code == 204, r.text

    # Re-enable TOTP — old cookie must not work even though we're back to
    # TOTP-enabled.
    r = client.post("/v1/auth/totp/setup")
    new_totp = pyotp.TOTP(r.json()["secret"])
    r = client.post("/v1/auth/totp/verify", json={"code": new_totp.now()})
    assert r.status_code == 204, r.text

    del client.headers["Authorization"]
    r = client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        cookies={"sheaf_trusted_device": cookie},
    )
    assert r.status_code == 401
    assert r.headers.get("X-Sheaf-2FA") == "required"


def test_list_and_revoke_trusted_device(client: httpx.Client):
    email = f"rd-list-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "testpassword123"
    token = _register_and_login(client, email, password)
    client.headers["Authorization"] = f"Bearer {token}"
    totp = _enrol_totp(client)
    r = client.post(
        "/v1/auth/login",
        json={
            "email": email, "password": password,
            "totp_code": totp.now(), "remember_device": True,
        },
    )
    cookie = r.cookies.get("sheaf_trusted_device")
    assert cookie

    # List shows one device, marked is_current when the cookie is sent.
    r = client.get(
        "/v1/auth/trusted-devices",
        cookies={"sheaf_trusted_device": cookie},
    )
    assert r.status_code == 200
    devices = r.json()
    assert len(devices) == 1
    assert devices[0]["is_current"] is True

    # Revoke it; subsequent login with the cookie no longer bypasses.
    device_id = devices[0]["id"]
    r = client.delete(
        f"/v1/auth/trusted-devices/{device_id}",
        cookies={"sheaf_trusted_device": cookie},
    )
    assert r.status_code == 204

    del client.headers["Authorization"]
    r = client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
        cookies={"sheaf_trusted_device": cookie},
    )
    assert r.status_code == 401


def test_change_password_revokes_other_sessions(client: httpx.Client):
    email = f"chpw-rev-{uuid.uuid4().hex[:8]}@sheaf.dev"
    password = "oldpassword123"

    # Session A: register (also logs in). Cookies are Secure, so over plain
    # HTTP httpx won't auto-send them — pull the session id out of the
    # response and pass it explicitly on later requests.
    r = client.post("/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201
    access_a = r.json()["access_token"]
    session_a = r.cookies.get("sheaf_session")
    assert session_a

    # Session B: log in from a separate client.
    with httpx.Client(base_url=str(client.base_url)) as other:
        r = other.post("/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200
        refresh_b = r.json()["refresh_token"]

        # Session A changes the password (bearer + session cookie).
        resp = client.post(
            "/v1/auth/change-password",
            json={"current_password": password, "new_password": "newpassword456"},
            headers={"Authorization": f"Bearer {access_a}"},
            cookies={"sheaf_session": session_a},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["revoked_other_sessions"] >= 1

        # Session B's refresh token now fails — its session was wiped, so
        # /refresh's session-existence check misses.
        r = other.post("/v1/auth/refresh", json={"refresh_token": refresh_b})
        assert r.status_code == 401
