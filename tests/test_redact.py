"""Unit tests for the email-redaction log helper."""

from sheaf.redact import redact_email


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


def test_full_address_never_appears_verbatim():
    addr = "verysecret@hidden.example"
    assert "verysecret" not in redact_email(addr)
