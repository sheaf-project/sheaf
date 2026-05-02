"""Render an outbox event payload into a recipient-facing message.

One outbox row carries the full before/after fronting set; this module
collapses that into one message per channel, filtered by the channel's
trigger flags + per-member visibility + redaction policy. Empty messages
(triggers don't match anything visible) become `suppress=True` so the
dispatcher drops them without delivery.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sheaf.models.notification_channel import (
    CofrontRedaction,
    NotificationChannel,
    PayloadSensitivity,
)


@dataclass(frozen=True, slots=True)
class RenderedMessage:
    title: str
    body: str
    # Whether the event should be suppressed entirely (channel's triggers
    # don't match anything visible to the recipient, or the redaction policy
    # is `suppress` and an invisible member would otherwise need to appear).
    suppress: bool = False


SUPPRESSED = RenderedMessage(title="", body="", suppress=True)


def _name(member_names: dict[uuid.UUID, str], mid: uuid.UUID) -> str:
    return member_names.get(mid, "(unknown)")


def _join_names(
    members: list[uuid.UUID], member_names: dict[uuid.UUID, str]
) -> str:
    """Comma-and-style join: A / A and B / A, B, and C."""
    names = [_name(member_names, m) for m in members]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _redacted_others(count: int, redaction: CofrontRedaction) -> str:
    """How to refer to N invisible members in prose. Caller has already
    decided the count > 0 and the redaction policy isn't `suppress`."""
    if redaction == CofrontRedaction.SOMEONE:
        return "someone" if count == 1 else f"{count} others"
    return "1 other" if count == 1 else f"{count} others"


def _phrase(
    visible: list[uuid.UUID],
    invisible_count: int,
    member_names: dict[uuid.UUID, str],
    redaction: CofrontRedaction,
) -> str:
    """Build a noun phrase covering visible + invisible participants."""
    visible_part = _join_names(visible, member_names)
    if invisible_count == 0:
        return visible_part
    redacted = _redacted_others(invisible_count, redaction)
    if not visible_part:
        return redacted
    if len(visible) == 1:
        return f"{visible_part} and {redacted}"
    return f"{visible_part}, and {redacted}"


def _cofront_changed_for(
    mid: uuid.UUID,
    before_ids: set[uuid.UUID],
    after_ids: set[uuid.UUID],
) -> bool:
    if mid not in before_ids or mid not in after_ids:
        return False
    return (before_ids - {mid}) != (after_ids - {mid})


def render_message(
    channel: NotificationChannel,
    *,
    payload: dict,
    member_names: dict[uuid.UUID, str],
    visible_member_ids: set[uuid.UUID],
) -> RenderedMessage:
    """Render the channel's view of an aggregated front-change payload.

    Payload shape:
        {"fronting_before": [member_id, ...],
         "fronting_after":  [member_id, ...]}
    """
    sensitivity = PayloadSensitivity(channel.payload_sensitivity)
    redaction = CofrontRedaction(channel.cofront_redaction)

    if sensitivity == PayloadSensitivity.BARE:
        return RenderedMessage(title="Front update", body="A front changed.")

    before_ids = {uuid.UUID(s) for s in payload.get("fronting_before", [])}
    after_ids = {uuid.UUID(s) for s in payload.get("fronting_after", [])}

    started = sorted(after_ids - before_ids)
    stopped = sorted(before_ids - after_ids)
    persisted = sorted(before_ids & after_ids)

    if sensitivity == PayloadSensitivity.MINIMAL:
        return _render_minimal(
            channel,
            started=started,
            stopped=stopped,
            persisted=persisted,
            before_ids=before_ids,
            after_ids=after_ids,
        )

    return _render_full(
        channel,
        started=started,
        stopped=stopped,
        persisted=persisted,
        before_ids=before_ids,
        after_ids=after_ids,
        visible_member_ids=visible_member_ids,
        member_names=member_names,
        redaction=redaction,
    )


def _render_minimal(
    channel: NotificationChannel,
    *,
    started: list[uuid.UUID],
    stopped: list[uuid.UUID],
    persisted: list[uuid.UUID],
    before_ids: set[uuid.UUID],
    after_ids: set[uuid.UUID],
) -> RenderedMessage:
    """Names hidden; just counts and which classes of change happened."""
    bits: list[str] = []
    if channel.trigger_on_start and started:
        bits.append(
            "someone started fronting"
            if len(started) == 1
            else f"{len(started)} members started fronting"
        )
    if channel.trigger_on_stop and stopped:
        bits.append(
            "someone stopped fronting"
            if len(stopped) == 1
            else f"{len(stopped)} members stopped fronting"
        )
    if (
        channel.trigger_on_cofront_change
        and not bits
        and any(
            _cofront_changed_for(m, before_ids, after_ids) for m in persisted
        )
    ):
        # Only mention cofront-only changes if start/stop didn't already
        # cover the transition.
        bits.append("the set of co-fronters changed")
    if not bits:
        return SUPPRESSED
    body = "; ".join(bits)
    return RenderedMessage(
        title="Front update", body=body[0].upper() + body[1:] + "."
    )


def _render_full(
    channel: NotificationChannel,
    *,
    started: list[uuid.UUID],
    stopped: list[uuid.UUID],
    persisted: list[uuid.UUID],
    before_ids: set[uuid.UUID],
    after_ids: set[uuid.UUID],
    visible_member_ids: set[uuid.UUID],
    member_names: dict[uuid.UUID, str],
    redaction: CofrontRedaction,
) -> RenderedMessage:
    sentences: list[str] = []

    if channel.trigger_on_start and started:
        visible = [m for m in started if m in visible_member_ids]
        invisible = len(started) - len(visible)
        if invisible > 0 and redaction == CofrontRedaction.SUPPRESS:
            return SUPPRESSED
        sentences.append(
            f"{_phrase(visible, invisible, member_names, redaction)} "
            "started fronting."
        )

    if channel.trigger_on_stop and stopped:
        visible = [m for m in stopped if m in visible_member_ids]
        invisible = len(stopped) - len(visible)
        if invisible > 0 and redaction == CofrontRedaction.SUPPRESS:
            return SUPPRESSED
        sentences.append(
            f"{_phrase(visible, invisible, member_names, redaction)} "
            "stopped fronting."
        )

    # Cofront-state sentences only make sense when start/stop didn't already
    # carry the news. If a member started or stopped, the recipient can
    # already infer who's fronting alongside whom from those sentences plus
    # their knowledge of the previous state.
    if channel.trigger_on_cofront_change and not sentences:
        for mid in persisted:
            if mid not in visible_member_ids:
                continue
            if not _cofront_changed_for(mid, before_ids, after_ids):
                continue
            new_co = sorted(after_ids - {mid})
            new_visible = [m for m in new_co if m in visible_member_ids]
            new_invisible = len(new_co) - len(new_visible)
            if new_invisible > 0 and redaction == CofrontRedaction.SUPPRESS:
                return SUPPRESSED
            watched_name = _name(member_names, mid)
            if not new_co:
                sentences.append(f"{watched_name} is fronting alone.")
                continue
            phrase = _phrase(
                new_visible, new_invisible, member_names, redaction
            )
            sentences.append(
                f"{watched_name} is now co-fronting with {phrase}."
            )

    if not sentences:
        return SUPPRESSED
    return RenderedMessage(title="Front update", body=" ".join(sentences))
