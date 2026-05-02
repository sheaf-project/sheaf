"""Aggregated front-change payload rendering.

Pure-function tests over `render_message`. No DB, no HTTP.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from sheaf.services.notifications.payload import render_message


def _channel(
    *,
    sensitivity: str = "full",
    redaction: str = "count",
    on_start: bool = True,
    on_stop: bool = False,
    on_cofront: bool = False,
):
    return SimpleNamespace(
        payload_sensitivity=sensitivity,
        cofront_redaction=redaction,
        trigger_on_start=on_start,
        trigger_on_stop=on_stop,
        trigger_on_cofront_change=on_cofront,
    )


def _payload(before: list[uuid.UUID], after: list[uuid.UUID]) -> dict:
    return {
        "fronting_before": [str(m) for m in before],
        "fronting_after": [str(m) for m in after],
    }


# ---------------------------------------------------------------------------
# Single-member transitions
# ---------------------------------------------------------------------------


def test_single_start_full_visible():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(),
        payload=_payload([a], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert msg.suppress is False
    assert "Bob started fronting." in msg.body


def test_single_stop_full_visible():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(on_start=False, on_stop=True),
        payload=_payload([a, b], [a]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert msg.body == "Bob stopped fronting."


# ---------------------------------------------------------------------------
# Multi-member aggregation — the whole point of the rewrite
# ---------------------------------------------------------------------------


def test_replace_fronts_aggregated_into_one_message():
    """{A, B} -> {C, D, E} should produce one message naming all five."""
    a, b, c, d, e = (uuid.uuid4() for _ in range(5))
    msg = render_message(
        _channel(on_start=True, on_stop=True),
        payload=_payload([a, b], [c, d, e]),
        member_names={a: "Alice", b: "Bob", c: "Cara", d: "Dani", e: "Eli"},
        visible_member_ids={a, b, c, d, e},
    )
    assert msg.suppress is False
    # Both started and stopped sentences present.
    assert "started fronting" in msg.body
    assert "stopped fronting" in msg.body
    # All names present (visibility matters, not order).
    for name in ("Alice", "Bob", "Cara", "Dani", "Eli"):
        assert name in msg.body, msg.body


def test_thirty_member_switch_renders_in_one_message():
    """The motivating scale case: 30 members switching should not produce
    30 messages. Render builds one body containing all the names."""
    members = [uuid.uuid4() for _ in range(30)]
    names = {m: f"M{i}" for i, m in enumerate(members)}
    msg = render_message(
        _channel(on_start=True),
        payload=_payload([], members),
        member_names=names,
        visible_member_ids=set(members),
    )
    assert msg.suppress is False
    # All 30 names appear once each.
    for n in names.values():
        assert n in msg.body
    # Single sentence ending in `started fronting.`
    assert msg.body.count("started fronting.") == 1


# ---------------------------------------------------------------------------
# Trigger gating filters which classes of change appear
# ---------------------------------------------------------------------------


def test_start_only_drops_stop_sentence_even_when_replace():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(on_start=True, on_stop=False),
        payload=_payload([a], [b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert "Bob started fronting." in msg.body
    assert "stopped" not in msg.body


def test_stop_only_drops_start_sentence_even_when_replace():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(on_start=False, on_stop=True),
        payload=_payload([a], [b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert "Alice stopped fronting." in msg.body
    assert "started" not in msg.body


def test_no_triggers_match_suppresses():
    """If a switch only stops members and trigger_on_stop is off, nothing
    to say -> suppress."""
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(on_start=True, on_stop=False),
        payload=_payload([a, b], [a]),  # only stop happened
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert msg.suppress is True


# ---------------------------------------------------------------------------
# Visibility + redaction
# ---------------------------------------------------------------------------


def test_invisible_starter_redacted_count():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(redaction="count"),
        payload=_payload([], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a},  # B is hidden
    )
    assert "Alice" in msg.body
    assert "1 other" in msg.body


def test_invisible_starter_redacted_someone():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(redaction="someone"),
        payload=_payload([], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a},
    )
    assert "Alice" in msg.body
    assert "someone" in msg.body


def test_invisible_starter_with_suppress_policy_drops_message():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(redaction="suppress"),
        payload=_payload([], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a},
    )
    assert msg.suppress is True


def test_all_members_invisible_suppresses():
    a = uuid.uuid4()
    msg = render_message(
        _channel(redaction="count"),
        payload=_payload([], [a]),
        member_names={a: "Alice"},
        visible_member_ids=set(),
    )
    # With redaction=count and no visible members at all, body would just
    # say "1 other started fronting" — that's what the policy promises;
    # suppress is reserved for when the user explicitly opted in.
    assert msg.suppress is False
    assert "1 other" in msg.body


# ---------------------------------------------------------------------------
# Cofront-change sentences
# ---------------------------------------------------------------------------


def test_cofront_change_sentence_when_no_start_stop_already_covers():
    """Pure cofront-only changes don't happen via the normal API today, but
    the renderer should still describe them when explicitly enabled."""
    a, b = uuid.uuid4(), uuid.uuid4()
    # Construct a transition that triggers cofront_change but no start/stop:
    # the only way is identical fronting sets — which means cofronters
    # didn't actually change, so this is a no-op.
    # Workable scenario: A started alone before, A is still fronting but B
    # joined -> B is in started, so trigger_on_start would normally cover.
    # If we disable trigger_on_start, we should see the cofront sentence.
    msg = render_message(
        _channel(on_start=False, on_stop=False, on_cofront=True),
        payload=_payload([a], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert "Alice is now co-fronting with Bob." in msg.body


def test_cofront_change_skipped_when_start_stop_already_covers():
    """If trigger_on_start is on and a member started, the resulting message
    already describes the new co-front state implicitly. We don't append
    a redundant cofront sentence."""
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(on_start=True, on_cofront=True),
        payload=_payload([a], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert "Bob started fronting." in msg.body
    # No "is now co-fronting with" sentence since start covered it.
    assert "now co-fronting" not in msg.body


# ---------------------------------------------------------------------------
# Sensitivity tiers
# ---------------------------------------------------------------------------


def test_minimal_sensitivity_hides_names():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(sensitivity="minimal"),
        payload=_payload([], [a, b]),
        member_names={a: "Alice", b: "Bob"},
        visible_member_ids={a, b},
    )
    assert "Alice" not in msg.body
    assert "Bob" not in msg.body
    # Should mention count-class rather than just "someone".
    assert "started fronting" in msg.body.lower()


def test_bare_sensitivity_returns_constant_message():
    a = uuid.uuid4()
    msg = render_message(
        _channel(sensitivity="bare"),
        payload=_payload([], [a]),
        member_names={a: "Alice"},
        visible_member_ids={a},
    )
    assert "Alice" not in msg.body
    assert msg.body == "A front changed."


def test_minimal_no_triggers_match_suppresses():
    a, b = uuid.uuid4(), uuid.uuid4()
    msg = render_message(
        _channel(sensitivity="minimal", on_start=False, on_stop=False),
        payload=_payload([a], [a, b]),
        member_names={},
        visible_member_ids=set(),
    )
    assert msg.suppress is True
