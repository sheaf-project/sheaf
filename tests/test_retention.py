import httpx


def test_retention_requires_admin(auth_client: httpx.Client):
    """Non-admin users should get 403."""
    resp = auth_client.post("/v1/admin/retention/run")
    assert resp.status_code == 403


def test_retention_unauthenticated(client: httpx.Client):
    resp = client.post("/v1/admin/retention/run")
    assert resp.status_code in (401, 403)
