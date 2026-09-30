import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, LargeBinary, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base, TimestampMixin, UUIDMixin


class PasskeyCredential(UUIDMixin, TimestampMixin, Base):
    """One WebAuthn credential (a passkey or a hardware key) enrolled by a user.

    The authenticator keeps the private key; this row holds only the public
    half and what a ceremony needs to verify a signature and to know which
    key signed. A passkey signs a user in on its own, so a row here is a
    credential in the same sense a password hash is, and the same rules
    around it apply (the account keeps its password no matter how many of
    these exist; see the passkey design).

    Nothing here is encrypted, deliberately. A public key is public and a
    credential ID is a handle the browser presents in the clear on every
    sign-in, not a secret. Marking either as an encrypted cell would pull it
    into the re-encryption sweep and imply a confidentiality property the
    data does not have.

    `rp_id` records the relying-party ID the credential was created under.
    A credential is bound to that name forever, so after a domain move only
    rows matching the instance's current RP ID are offered; the rest stay
    listed in settings with an explanation rather than failing in a way
    nobody can diagnose.
    """

    __tablename__ = "passkey_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    # The authenticator's identifier for this key, raw bytes as the browser
    # presents them. Unique across the instance: it is the sign-in lookup
    # key for a discoverable credential, where no email is typed first.
    credential_id: Mapped[bytes] = mapped_column(
        LargeBinary, nullable=False, unique=True, index=True,
    )
    # COSE-encoded public key, exactly as attested at registration.
    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    # The authenticator's signature counter as last seen. Unsigned 32-bit on
    # the wire, hence BigInteger. Synced passkeys always report zero, so a
    # regression is only suspicious when the stored value is above zero.
    sign_count: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0",
    )
    rp_id: Mapped[str] = mapped_column(String(253), nullable=False)
    # Transport hints the client reported at registration ("internal",
    # "usb", "hybrid", ...), echoed back in allow lists so the browser can
    # prompt for the right kind of authenticator. Advisory only.
    transports: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]",
    )
    # Authenticator model identifier from the attestation, when the
    # authenticator supplied one. Informational (a settings list can name the
    # key model); never a trust decision.
    aaguid: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # The backup flags from the authenticator data: whether this credential
    # can be synced to other devices, and whether it currently is. Shown in
    # settings so a user can tell a synced passkey from a single hardware key.
    backup_eligible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    backup_state: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false",
    )
    nickname: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    last_used_ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
