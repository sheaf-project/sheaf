"""Sanity check the RFC 9116 security.txt endpoint."""

import re

import httpx


def test_well_known_security_txt(client: httpx.Client):
    resp = client.get("/.well-known/security.txt")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    body = resp.text
    # Required fields per RFC 9116.
    assert re.search(r"^Contact: mailto:", body, re.MULTILINE)
    assert re.search(r"^Expires: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", body, re.MULTILINE)
    # Recommended extras we've committed to.
    assert "Preferred-Languages: en" in body
    assert "Policy:" in body
    assert "Encryption:" in body


def test_security_txt_legacy_path_also_served(client: httpx.Client):
    """RFC 9116 deprecated /security.txt but legacy clients still hit it."""
    resp = client.get("/security.txt")
    assert resp.status_code == 200
    assert "Contact: mailto:" in resp.text


def test_security_txt_no_auth_required(client: httpx.Client):
    """Endpoint must be reachable without any auth headers."""
    # client fixture is unauthenticated — verify status explicitly.
    resp = client.get(
        "/.well-known/security.txt",
        headers={"Authorization": ""},
    )
    assert resp.status_code == 200
