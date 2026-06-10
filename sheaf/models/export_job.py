import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base, UUIDMixin


class ExportJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    EXPIRED = "expired"


class ExportJob(UUIDMixin, Base):
    """Async data-export request.

    Created by the user via POST /v1/export/jobs. The dispatcher worker
    picks up pending rows, builds a zip (JSON + optionally referenced
    image blobs), uploads to S3 (or writes to local disk), and marks the
    row done with a TTL. A separate cleanup worker prunes expired rows
    and their underlying files.
    """

    __tablename__ = "export_jobs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    include_images: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )

    # State machine: pending -> running -> done | failed; done -> expired
    # after the file is cleaned up. EXPIRED rows stick around so the user
    # can see the historical request even after the file is gone.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ExportJobStatus.PENDING
    )

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Where the artefact lives. For S3: an object key relative to the
    # configured export bucket. For filesystem: an absolute path under
    # /app/data/exports. Null until the worker finishes.
    file_location: Mapped[str | None] = mapped_column(String(500), nullable=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bumped each time the stale-RUNNING sweep has to reset this job after
    # a crashed/deployed-over build; parks the job as FAILED at the cap so
    # a poisoned export can't crash-loop the worker.
    failed_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    user: Mapped["User"] = relationship()  # noqa: F821

    __table_args__ = (
        Index("ix_export_jobs_user_requested", "user_id", "requested_at"),
        # Worker claim: pending jobs in FIFO order.
        Index(
            "ix_export_jobs_pending",
            "requested_at",
            postgresql_where="status = 'pending'",
        ),
        # Cleanup sweep: jobs whose file has expired but row hasn't been
        # marked yet.
        Index("ix_export_jobs_expires", "expires_at"),
    )
