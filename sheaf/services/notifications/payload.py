"""Render an outbox event payload into a recipient-facing message.

Applies payload sensitivity (`full` / `minimal` / `bare`) and co-front
redaction (`count` / `someone` / `suppress`) per the design doc.
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
    # Whether the event should be suppressed entirely (used for `suppress`
    # cofront redaction when an invisible co-fronter is present).
    suppress: bool = False


def _member_name(member_names: dict[uuid.UUID, str], mid: uuid.UUID) -> str:
    return member_names.get(mid, "(unknown)")


def render_message(
    channel: NotificationChannel,
    *,
    payload: dict,
    member_names: dict[uuid.UUID, str],
    visible_member_ids: set[uuid.UUID],
) -> RenderedMessage:
    """Render the channel's view of `payload` given which members the
    recipient is allowed to see by name. Caller has already decided that
    the watched member is visible (otherwise we wouldn't be dispatching).
    """
    sensitivity = PayloadSensitivity(channel.payload_sensitivity)
    redaction = CofrontRedaction(channel.cofront_redaction)

    kind = payload["kind"]
    watched_id = uuid.UUID(payload["member_id"])
    watched_name = _member_name(member_names, watched_id)

    if sensitivity == PayloadSensitivity.BARE:
        return RenderedMessage(title="Front update", body="A front changed.")

    if kind == "start":
        if sensitivity == PayloadSensitivity.MINIMAL:
            return RenderedMessage(title="Front started", body="Someone started fronting.")
        return RenderedMessage(
            title="Front started", body=f"{watched_name} started fronting."
        )

    if kind == "stop":
        if sensitivity == PayloadSensitivity.MINIMAL:
            return RenderedMessage(title="Front ended", body="Someone stopped fronting.")
        return RenderedMessage(
            title="Front ended", body=f"{watched_name} stopped fronting."
        )

    if kind == "cofront_change":
        # `minimal` mode: never name co-fronters at all.
        if sensitivity == PayloadSensitivity.MINIMAL:
            return RenderedMessage(
                title="Co-front change",
                body="The set of co-fronters changed.",
            )

        # `full` mode: apply cofront_redaction to invisible co-fronters.
        cofronters_after_ids = [uuid.UUID(s) for s in payload.get("cofronters_after", [])]
        visible_co = [m for m in cofronters_after_ids if m in visible_member_ids]
        invisible_co = [m for m in cofronters_after_ids if m not in visible_member_ids]

        if invisible_co and redaction == CofrontRedaction.SUPPRESS:
            return RenderedMessage(title="", body="", suppress=True)

        visible_names = ", ".join(_member_name(member_names, m) for m in visible_co)
        n_invisible = len(invisible_co)

        if not invisible_co:
            if not visible_co:
                body = f"{watched_name} is fronting alone."
            else:
                body = f"{watched_name} is now co-fronting with {visible_names}."
            return RenderedMessage(title="Co-front change", body=body)

        # Mixed or all-invisible: redact per policy.
        if redaction == CofrontRedaction.SOMEONE:
            redacted = (
                "someone"
                if n_invisible == 1
                else f"{n_invisible} others"
            )
        else:  # COUNT
            redacted = (
                "1 other"
                if n_invisible == 1
                else f"{n_invisible} others"
            )

        if visible_co:
            body = (
                f"{watched_name} is now co-fronting with {visible_names} "
                f"and {redacted}."
            )
        else:
            body = f"{watched_name} is now co-fronting with {redacted}."
        return RenderedMessage(title="Co-front change", body=body)

    return RenderedMessage(title="Front update", body="A front changed.")
