"""Unit tests for the SMTP2GO webhook event mapping.

SMTP2GO doesn't sign payloads, so there's no crypto to verify (the
endpoint guards on a shared URL secret). The bug-prone part is the
event -> deliverability-action mapping, which is a pure function tested
here. The apply_* state transitions it dispatches to are covered in
test_email_events.py.
"""

import pytest

from sheaf.api.v1.webhooks import parse_smtp2go_payload, smtp2go_event_action


def test_delivered_maps_to_delivered():
    assert smtp2go_event_action("delivered", None) == "delivered"


def test_hard_bounce_maps_to_hard():
    assert smtp2go_event_action("bounce", "hard") == "hard_bounce"


def test_soft_bounce_maps_to_soft():
    assert smtp2go_event_action("bounce", "soft") == "soft_bounce"


def test_unclassified_bounce_defaults_to_soft():
    # Conservative: an unknown/missing bounce classification must not
    # immediately hard-block. Soft only blocks past the threshold.
    assert smtp2go_event_action("bounce", None) == "soft_bounce"
    assert smtp2go_event_action("bounce", "weird") == "soft_bounce"


def test_spam_maps_to_complaint():
    assert smtp2go_event_action("spam", None) == "complaint"


def test_reject_is_ignored():
    # SMTP2GO emits `reject` when refusing to send to an already-flagged
    # address - no new state, so no action.
    assert smtp2go_event_action("reject", None) is None


def test_non_actionable_events_ignored():
    for ev in ("processed", "open", "click", "unsubscribe", "resubscribe"):
        assert smtp2go_event_action(ev, None) is None


def test_unknown_event_ignored():
    assert smtp2go_event_action("totally_made_up", None) is None
    assert smtp2go_event_action("", None) is None


# --- payload parsing (JSON or form-encoded, operator-configurable) ---------


def test_parse_json_single_object():
    body = b'{"event":"bounce","rcpt":"x@example.com","bounce":"hard"}'
    events = parse_smtp2go_payload(body, "application/json")
    assert events == [
        {"event": "bounce", "rcpt": "x@example.com", "bounce": "hard"}
    ]


def test_parse_json_array():
    body = b'[{"event":"delivered","rcpt":"a@x.com"},{"event":"spam","rcpt":"b@x.com"}]'
    events = parse_smtp2go_payload(body, "application/json")
    assert len(events) == 2
    assert events[0]["event"] == "delivered"
    assert events[1]["event"] == "spam"


def test_parse_json_without_content_type_sniffed_by_shape():
    # SMTP2GO doesn't always set a JSON content-type; a body that opens
    # like JSON is still parsed as JSON.
    body = b'{"event":"delivered","rcpt":"a@x.com"}'
    events = parse_smtp2go_payload(body, "")
    assert events[0]["event"] == "delivered"


def test_parse_form_encoded_single_event():
    body = b"event=bounce&rcpt=x%40example.com&bounce=soft"
    events = parse_smtp2go_payload(
        body, "application/x-www-form-urlencoded"
    )
    assert events == [
        {"event": "bounce", "rcpt": "x@example.com", "bounce": "soft"}
    ]


def test_parse_empty_body_raises():
    with pytest.raises(ValueError):
        parse_smtp2go_payload(b"   ", "application/json")


def test_parse_garbage_json_raises():
    with pytest.raises(ValueError):
        parse_smtp2go_payload(b"{not valid json", "application/json")
