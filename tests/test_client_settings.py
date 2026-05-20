"""Tests for the client-settings endpoints, focused on the PATCH merge.

PATCH does an atomic top-level key merge so independent callers each
writing their own key can't clobber one another the way concurrent
full-blob PUTs would.
"""

import httpx


def test_put_then_get_roundtrips(auth_client: httpx.Client):
    resp = auth_client.put(
        "/v1/settings/client/web",
        json={"settings": {"theme": "dark", "fronts": {"view": "paged"}}},
    )
    assert resp.status_code == 200, resp.text
    got = auth_client.get("/v1/settings/client/web").json()
    assert got["settings"] == {"theme": "dark", "fronts": {"view": "paged"}}


def test_patch_merges_into_existing(auth_client: httpx.Client):
    auth_client.put(
        "/v1/settings/client/web",
        json={"settings": {"theme": "dark", "onboarding_complete": False}},
    )
    resp = auth_client.patch(
        "/v1/settings/client/web",
        json={"settings": {"onboarding_complete": True, "new_key": 1}},
    )
    assert resp.status_code == 200, resp.text
    merged = resp.json()["settings"]
    # theme survives; the patched keys win / are added.
    assert merged == {"theme": "dark", "onboarding_complete": True, "new_key": 1}


def test_patch_creates_when_absent(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/settings/client/fresh-client",
        json={"settings": {"hello": "world"}},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["settings"] == {"hello": "world"}


def test_patch_independent_keys_do_not_clobber(auth_client: httpx.Client):
    """The race-fix property: two callers each patching their own key both
    survive, where two full-blob PUTs would have lost one write."""
    auth_client.patch(
        "/v1/settings/client/web",
        json={"settings": {"fronts": {"view": "paged"}}},
    )
    auth_client.patch(
        "/v1/settings/client/web",
        json={"settings": {"dismissed_announcements": ["a1"]}},
    )
    final = auth_client.get("/v1/settings/client/web").json()["settings"]
    assert final["fronts"] == {"view": "paged"}
    assert final["dismissed_announcements"] == ["a1"]


def test_patch_oversize_rejected(auth_client: httpx.Client):
    resp = auth_client.patch(
        "/v1/settings/client/web",
        json={"settings": {"blob": "x" * (17 * 1024)}},
    )
    assert resp.status_code == 413, resp.text
