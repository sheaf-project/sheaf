"""Tests for the operator's public abuse/DMCA contact.

The value is one setting read straight into the unauthenticated config
payload, so the set/empty behaviour is exercised in-process here: the test
stack runs the server in a separate process where monkeypatching settings
would not reach it. The server-facing check is just that the config endpoint
carries the key.
"""

import asyncio

import httpx
import pytest

from sheaf.api.v1.auth import get_auth_config
from sheaf.config import Settings, settings


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


@pytest.mark.parametrize("field", ["public_abuse_contact", "support_note"])
def test_literal_newline_escape_is_decoded(field):
    """A `\\n` typed into an env var becomes a real newline.

    Regression: the docs tell operators to write the multi-line form as a
    quoted string with `\\n` escapes, but whether those survive as escapes
    depends on what loaded the env (compose's env_file parser and
    python-dotenv decode them; a stack manager's environment textbox and
    `docker run -e` do not). Instances on the second kind rendered a literal
    backslash-n in the abuse/DMCA popup.
    """
    raw = "Report abuse: abuse@example.net\\nDMCA agent: Someone, somewhere."
    value = getattr(Settings(**{field: raw}), field)
    assert value == "Report abuse: abuse@example.net\nDMCA agent: Someone, somewhere."


@pytest.mark.parametrize("field", ["public_abuse_contact", "support_note"])
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Already decoded by the loader: nothing left to find, so unchanged.
        # This is the property that makes the decode safe to apply blind.
        ("real\nnewlines\nhere", "real\nnewlines\nhere"),
        # Escaping the backslash is how an operator asks for the two
        # characters, so that must survive.
        ("literal \\\\n please", "literal \\n please"),
        # Tabs for an indented address block; carriage returns because
        # someone will paste from Windows.
        ("name\\tvalue", "name\tvalue"),
        # Not in the table: left exactly as typed rather than swallowed.
        ("50% off \\q things", "50% off \\q things"),
        # A trailing backslash is a syntax error to unicode_escape, which is
        # why this does not use it.
        ("ends with a backslash \\", "ends with a backslash \\"),
        # Non-ASCII passes through intact (the other unicode_escape hazard).
        ("café ✨\\nnext line", "café ✨\nnext line"),
        ("", ""),
    ],
)
def test_escape_decoding_edge_cases(field, raw, expected):
    assert getattr(Settings(**{field: raw}), field) == expected


def test_config_endpoint_exposes_key(client: httpx.Client):
    resp = client.get("/v1/auth/config")
    assert resp.status_code == 200
    # Always present; null unless the operator set the text (the test stack
    # does not, so we only assert the contract, not a value).
    assert "abuse_contact" in resp.json()
