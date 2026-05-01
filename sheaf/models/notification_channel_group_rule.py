import enum
import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sheaf.models.base import Base


class GroupRuleAction(enum.StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"


class IncludePrivate(enum.StrEnum):
    INHERIT = "inherit"
    YES = "yes"
    NO = "no"


class NotificationChannelGroupRule(Base):
    __tablename__ = "notification_channel_group_rules"

    channel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        primary_key=True,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("groups.id", ondelete="CASCADE"),
        primary_key=True,
    )
    rule: Mapped[str] = mapped_column(String(8), nullable=False)
    include_private: Mapped[str] = mapped_column(
        String(8), nullable=False, default="inherit", server_default="inherit"
    )

    channel: Mapped["NotificationChannel"] = relationship(back_populates="group_rules")
    group: Mapped["Group"] = relationship()

    __table_args__ = (
        CheckConstraint("rule IN ('include','exclude')", name="ck_group_rule_action"),
        CheckConstraint(
            "include_private IN ('inherit','yes','no')",
            name="ck_group_rule_include_private",
        ),
    )
