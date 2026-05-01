import enum
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base


class MemberRuleAction(enum.StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class NotificationChannelMemberRule(Base):
    __tablename__ = "notification_channel_member_rules"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    member_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("members.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule: Mapped[str] = mapped_column(String(8), nullable=False)

    channel: Mapped["NotificationChannel"] = relationship(back_populates="member_rules")
    member: Mapped["Member"] = relationship()

    __table_args__ = (
        CheckConstraint("rule IN ('include','exclude')", name="ck_member_rule_action"),
    )
