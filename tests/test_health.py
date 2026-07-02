"""Liveness and readiness probe tests.

/health is the always-200 liveness probe (existing infra depends on that
semantics). /health/ready actively checks Postgres and Redis and returns
503 if either is down - infra should point the readiness check at it.
"""

import httpx


def test_health_liveness(client: httpx.Client):
    """/health is unconditional 200 - it must not check dependencies."""
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_health_ready_ok(client: httpx.Client):
    """In the test stack both Postgres and Redis are up, so readiness
    reports 200 with each dependency marked ok."""
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"
