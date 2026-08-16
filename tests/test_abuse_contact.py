"""Tests for the operator's public abuse/DMCA contact.

The value is one setting read straight into the unauthenticated config
payload, so the set/empty behaviour is exercised in-process here: the test
stack runs the server in a separate process where monkeypatching settings
would not reach it. The server-facing check is just that the config endpoint
carries the key.
"""

import asyncio

import httpx

from sheaf.api.v1.auth import get_auth_config
from sheaf.config import settings


def _config() -> dict:
    return asyncio.run(get_auth_config())


def test_absent_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "public_abuse_contact", "")
    # The key is always present so a client can rely on its shape; null is
    # what tells the footer there is nothing to offer.
    assert _config()["abuse_contact"] is None


def test_present_when_set(monkeypatch):
    text = "Abuse: abuse@example.net\n\nDMCA agent: Someone, somewhere."
    monkeypatch.setattr(settings, "public_abuse_contact", text)
    # Passed through verbatim: it is markdown the operator wrote, rendered by
    # the same pipeline as a public bio, not something the API reformats.
    assert _config()["abuse_contact"] == text


def test_config_endpoint_exposes_key(client: httpx.Client):
    resp = client.get("/v1/auth/config")
    assert resp.status_code == 200
    # Always present; null unless the operator set the text (the test stack
    # does not, so we only assert the contract, not a value).
    assert "abuse_contact" in resp.json()
