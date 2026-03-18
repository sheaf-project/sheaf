import httpx


def test_retention_selfhosted_noop(auth_client: httpx.Client):
    """In self-hosted mode, retention returns 0 pruned."""
    resp = auth_client.post("/v1/admin/retention/run")
    assert resp.status_code == 200
    assert resp.json()["pruned"] == 0


def test_retention_unauthenticated(client: httpx.Client):
    resp = client.post("/v1/admin/retention/run")
    assert resp.status_code in (401, 403)
