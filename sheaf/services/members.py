"""Member encryption helpers.

Member.name and Member.description are encrypted at application level. This
module owns the small set of helpers that translate between the persisted
ciphertext columns and the plaintext used by API endpoints, services, and
schema serialisation.

Member.name_hash is a keyed blind index of the (normalised) plaintext name,
so exact-match lookups within a system stay possible without having to
decrypt the whole table.
"""

from __future__ import annotations

from datetime import datetime

from sheaf.crypto import blind_index, decrypt, encrypt
from sheaf.encrypted_fields import (
    member_description_aad,
    member_name_aad,
    member_note_aad,
)
from sheaf.models.member import Member
from sheaf.schemas.member import MemberRead


def member_name_plaintext(member: Member) -> str:
    return decrypt(member.name, aad=member_name_aad(member.id))


def member_description_plaintext(member: Member) -> str | None:
    if member.description is None:
        return None
    return decrypt(member.description, aad=member_description_aad(member.id))


def member_note_plaintext(member: Member) -> str | None:
    if member.note is None:
        return None
    return decrypt(member.note, aad=member_note_aad(member.id))


def set_member_name(member: Member, plaintext: str) -> None:
    """Encrypt + hash a new plaintext name onto a Member instance.

    The member must already carry its id (pre-allocated by the caller on an
    insert path) so the ciphertext binds to the right cell.
    """
    member.name = encrypt(plaintext, aad=member_name_aad(member.id))
    member.name_hash = blind_index(plaintext)


def set_member_description(member: Member, plaintext: str | None) -> None:
    member.description = (
        encrypt(plaintext, aad=member_description_aad(member.id))
        if plaintext is not None
        else None
    )


def set_member_note(member: Member, plaintext: str | None) -> None:
    """Set / clear the scratchpad note. Empty string normalises to None
    so deleting the contents in the UI clears the column rather than
    persisting an encrypted empty string."""
    if plaintext is None or plaintext == "":
        member.note = None
    else:
        member.note = encrypt(plaintext, aad=member_note_aad(member.id))


def decrypt_member_for_read(
    member: Member,
    *,
    has_bio_revisions: bool = False,
    pending_delete_at: datetime | None = None,
) -> MemberRead:
    """Build a MemberRead with name + description decrypted to plaintext.

    `has_bio_revisions` is opt-in: callers that need an accurate value
    (the /v1/members endpoints, since the bio history button reads it)
    look it up and pass through. Nested contexts (tag / group member
    lists) default to False; the bio history modal is opened from the
    members route, not from those, so a stale value there is harmless.

    `pending_delete_at` is the finalize_after timestamp from a queued
    MEMBER_DELETE pending action, or None. Same opt-in pattern - list
    endpoints look it up once per request and pass through; nested
    contexts pass None.
    """
    return MemberRead.model_validate({
        "id": member.id,
        "system_id": member.system_id,
        "name": member_name_plaintext(member),
        "display_name": member.display_name,
        "description": member_description_plaintext(member),
        "pronouns": member.pronouns,
        "avatar_url": member.avatar_url,
        "banner_url": member.banner_url,
        "color": member.color,
        "birthday": member.birthday,
        "pluralkit_id": member.pluralkit_id,
        "emoji": member.emoji,
        "is_custom_front": member.is_custom_front,
        "privacy": member.privacy,
        "note": member_note_plaintext(member),
        "quick_switch_pin": member.quick_switch_pin,
        "created_at": member.created_at,
        "updated_at": member.updated_at,
        "has_bio_revisions": has_bio_revisions,
        "pending_delete_at": pending_delete_at,
        "archived_at": member.archived_at,
    })


def member_plaintext(member: Member) -> tuple[str, str | None]:
    """Convenience: return (name, description) decrypted in one call."""
    return member_name_plaintext(member), member_description_plaintext(member)
