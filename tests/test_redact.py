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
