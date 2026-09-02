"""Unit tests for log-redaction helpers."""

import logging

from sheaf.redact import (
    ShareTokenAccessLogFilter,
    redact_email,
    redact_share_token_path,
)


def test_redacts_normal_address():
    assert redact_email("alice@example.com") == "a***e@example.com"


def test_short_local_part_collapses_to_star():
    assert redact_email("al@example.com") == "*@example.com"
    assert redact_email("a@example.com") == "*@example.com"


def test_three_char_local_keeps_first_and_last():
    assert redact_email("bob@example.com") == "b*b@example.com"


def test_domain_is_preserved():
    # The domain is the operationally useful part (which provider is
    # bouncing) and is kept verbatim.
    assert redact_email("someone@mail.example.co.uk").endswith(
        "@mail.example.co.uk"
    )


def test_no_at_sign_is_fully_redacted():
    assert redact_email("not-an-email") == "<redacted>"


def test_empty_and_none_are_redacted():
    assert redact_email("") == "<redacted>"
    assert redact_email(None) == "<redacted>"


def test_trailing_at_with_no_domain_is_redacted():
    assert redact_email("alice@") == "<redacted>"


def test_redacts_share_token_but_keeps_endpoint_and_query():
    assert redact_share_token_path(
        "/v1/public/shared/super-secret-token/members?preview=1"
    ) == "/v1/public/shared/<redacted>/members?preview=1"
    assert redact_share_token_path("/s/super-secret-token") == "/s/<redacted>"


def test_redacts_public_file_owner_and_token_but_keeps_prefix():
    # /v1/public/files/{prefix}/{owner}/{uuid} leaks the owner id in the path
    # and a live HMAC capability in the query. Both must go; the prefix, the
    # random filename and the expires timestamp are harmless triage aids.
    redacted = redact_share_token_path(
        "/v1/public/files/avatars/00000000-0000-0000-0000-000000000000/"
        "abc123.png?token=deadbeef&expires=1700000000"
    )
    assert "00000000-0000-0000-0000-000000000000" not in redacted
    assert "deadbeef" not in redacted
    assert redacted == (
        "/v1/public/files/avatars/<redacted>/"
        "abc123.png?token=<redacted>&expires=1700000000"
    )


def test_non_tokened_path_is_left_intact():
    assert redact_share_token_path("/v1/members/list?page=2") == (
        "/v1/members/list?page=2"
    )


def test_uvicorn_access_filter_redacts_public_file_owner_and_token():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1234",
            "GET",
            "/v1/public/files/banners/11111111-1111-1111-1111-111111111111/"
            "photo.jpg?token=secrethmac&expires=1700000000",
            "1.1",
            200,
        ),
        None,
    )

    assert ShareTokenAccessLogFilter().filter(record) is True
    message = record.getMessage()
    assert "11111111-1111-1111-1111-111111111111" not in message
    assert "secrethmac" not in message
    assert "expires=1700000000" in message


def test_uvicorn_access_filter_redacts_path_argument():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1234",
            "GET",
            "/v1/public/shared/super-secret-token/fronting",
            "1.1",
            200,
        ),
        None,
    )

    assert ShareTokenAccessLogFilter().filter(record) is True
    assert "super-secret-token" not in record.getMessage()
    assert "/v1/public/shared/<redacted>/fronting" in record.getMessage()


def test_full_address_never_appears_verbatim():
    addr = "verysecret@hidden.example"
    assert "verysecret" not in redact_email(addr)
