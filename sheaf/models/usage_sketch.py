from datetime import date, datetime

from sqlalchemy import Date, DateTime, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column

from sheaf.models.base import Base


class UsageDailySketch(Base):
    """Durable backing store for the per-day active-cardinality HLL sketches.

    This is aggregate OPERATIONS data, not user data. Each row holds the raw
    HyperLogLog register bytes for one (day, scope, auth_kind) - the account ids
    / system ids that were active that day are irreversibly folded into the
    registers, so a sketch can answer "roughly how many distinct ids" but can
    never enumerate them or answer "was id X active on day Y". Nothing
    per-account is stored, which is why this table is deliberately excluded from
    the Article 20 export (there is nothing user-attributable to hand back).

    scope is "acct" or "sys". auth_kind is "client" (session cookie or JWT
    bearer) or "api" (API key) - the two are kept in separate sketches because a
    distinct count cannot be sliced out of a merged sketch after the fact; the
    published "any" total is their read-time union and is never stored.

    Why persist the SKETCH BYTES and not just a daily count: a 30-day MAU is the
    cardinality of the UNION of 30 daily sketches, which cannot be reconstructed
    by summing daily unique counts (that double-counts returning users). Redis
    survives in-place upgrades but not an instance replace, so after a Redis
    replace the day-keys are gone; MAU is only recoverable if the mergeable
    sketches themselves were persisted here and can be RESTOREd and PFMERGEd.
    """

    __tablename__ = "usage_daily_sketches"

    # Composite natural key: one sketch per (day, scope, auth_kind). No surrogate
    # UUID - the triple IS the identity, and the flush job UPSERTs on it.
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    scope: Mapped[str] = mapped_column(String(8), primary_key=True)
    auth_kind: Mapped[str] = mapped_column(String(8), primary_key=True)

    # Raw HLL register bytes as GET off the Redis day-key. Round-trips through
    # SET back into Redis to become a mergeable sketch again after a replace.
    sketch: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
