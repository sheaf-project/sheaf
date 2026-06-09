"""GET /v1/systems/{id} is owner-only.

The endpoint used to return the full owner view (including the decrypted
private note and the delete_confirmation tier) for any system whose
privacy was set to `public`, to any authenticated caller. Until public
profiles ship as a designed feature with a dedicated public schema,
cross-tenant reads are closed entirely — `privacy` is settable but
grants nothing.
"""

import os
import uuid

import httpx

BASE_URL = os.environ.get("SHEAF_TEST_URL", "http://localhost:8000")


def _register(client: httpx.Client) -> str:
    email = f"sysprv-{uuid.uuid4().hex[:8]}@sheaf.dev"
    resp = client.post(
        "/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["access_token"]


def test_get_system_by_id_is_owner_only_even_when_public(client: httpx.Client):
    # User A: make their system public and stash a private note.
    with httpx.Client(base_url=BASE_URL) as a:
        a.headers["Authorization"] = f"Bearer {_register(a)}"
        me = a.get("/v1/systems/me")
        assert me.status_code == 200, me.text
        system_id = me.json()["id"]

        patch = a.patch(
            "/v1/systems/me",
            json={"privacy": "public", "note": "private scratchpad contents"},
        )
        assert patch.status_code == 200, patch.text

        # Owner can still fetch their own system by id.
        own = a.get(f"/v1/systems/{system_id}")
        assert own.status_code == 200, own.text
        assert own.json()["note"] == "private scratchpad contents"

    # User B: cross-tenant fetch 404s despite privacy=public, and the
    # response carries nothing distinguishable from "does not exist".
    with httpx.Client(base_url=BASE_URL) as b:
        b.headers["Authorization"] = f"Bearer {_register(b)}"
        resp = b.get(f"/v1/systems/{system_id}")
        assert resp.status_code == 404, resp.text
        assert "note" not in resp.text


def test_get_system_by_id_requires_auth(client: httpx.Client):
    resp = client.get(f"/v1/systems/{uuid.uuid4()}")
    assert resp.status_code in (401, 403)
