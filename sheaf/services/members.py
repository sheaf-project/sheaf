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

from sheaf.crypto import blind_index, decrypt, encrypt
from sheaf.models.member import Member
from sheaf.schemas.member import MemberRead


def member_name_plaintext(member: Member) -> str:
    return decrypt(member.name)


def member_description_plaintext(member: Member) -> str | None:
    if member.description is None:
        return None
    return decrypt(member.description)


def set_member_name(member: Member, plaintext: str) -> None:
    """Encrypt + hash a new plaintext name onto a Member instance."""
    member.name = encrypt(plaintext)
    member.name_hash = blind_index(plaintext)


def set_member_description(member: Member, plaintext: str | None) -> None:
    member.description = encrypt(plaintext) if plaintext is not None else None


def decrypt_member_for_read(member: Member) -> MemberRead:
    """Build a MemberRead with name + description decrypted to plaintext."""
    return MemberRead.model_validate({
        "id": member.id,
        "system_id": member.system_id,
        "name": member_name_plaintext(member),
        "display_name": member.display_name,
        "description": member_description_plaintext(member),
        "pronouns": member.pronouns,
        "avatar_url": member.avatar_url,
        "color": member.color,
        "birthday": member.birthday,
        "pluralkit_id": member.pluralkit_id,
        "emoji": member.emoji,
        "is_custom_front": member.is_custom_front,
        "privacy": member.privacy,
        "created_at": member.created_at,
        "updated_at": member.updated_at,
    })


def member_plaintext(member: Member) -> tuple[str, str | None]:
    """Convenience: return (name, description) decrypted in one call."""
    return member_name_plaintext(member), member_description_plaintext(member)
