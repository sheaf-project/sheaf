from datetime import date, datetime

from sqlalchemy import Date, DateTime, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base


class UsageDailySketch(Base):
    """Durable backing store for the per-day active-cardinality HLL sketches.

    This is aggregate OPERATIONS data, not user data. Each row holds the raw
    HyperLogLog register bytes for one (day, scope, auth_kind). What was folded
    into the registers is not the id but a keyed HMAC of it (see usage.py
    `_active_token`), so a sketch answers "roughly how many distinct ids" while a
    holder of these bytes can neither enumerate the ids nor test whether a known
    id was active without the server key. Nothing per-account is stored, which is
    why this table is deliberately excluded from the Article 20 export (there is
    nothing user-attributable to hand back).

    scope is "acct" or "sys". auth_kind is "client" (session cookie or JWT
    bearer) or "api" (API key) - the two are kept in separate sketches because a
    distinct count cannot be sliced out of a merged sketch after the fact; the
    published "any" total is their read-time union and is never stored.

    client_family is '' for the auth-kind sketch itself, or one of the
    interactive families (web / android / ios / watch / other) for the
    per-platform sketch under auth_kind "client". The family sketches are
    additive - the auth-kind sketch is still written on every request - so
    the DAU/MAU series never depend on them; they exist for platform share
    and, by inclusion-exclusion over their unions, cross-platform overlap.
    There is no "api" family row: the api auth-kind sketch IS the api family.

    Why persist the SKETCH BYTES and not just a daily count: a 30-day MAU is the
    cardinality of the UNION of 30 daily sketches, which cannot be reconstructed
    by summing daily unique counts (that double-counts returning users). Redis
    survives in-place upgrades but not an instance replace, so after a Redis
    replace the day-keys are gone; MAU is only recoverable if the mergeable
    sketches themselves were persisted here and can be RESTOREd and PFMERGEd.
    """

    __tablename__ = "usage_daily_sketches"

    # Composite natural key: one sketch per (day, scope, auth_kind,
    # client_family). No surrogate UUID - the tuple IS the identity, and the
    # flush job UPSERTs on it.
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)
    auth_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    client_family: Mapped[str] = mapped_column(
        String(16), primary_key=True, default="", server_default=""
    )

    # Raw HLL register bytes as GET off the Redis day-key. Round-trips through
    # SET back into Redis to become a mergeable sketch again after a replace.
    sketch: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
