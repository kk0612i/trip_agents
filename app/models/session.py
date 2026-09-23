"""多轮旅行会话的 ORM 映射。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, table_options


class ChatSession(Base):
    """多轮需求会话；活跃运行和最新运行是事务维护的逻辑指针。"""

    __tablename__ = "chat_session"
    __table_args__ = (
        Index("idx_chat_session_user_created", "user_id", "created_at", "session_id"),
        table_options("旅行会话"),
    )
    session_id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="会话 UUID")
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("app_user.id", name="fk_chat_session_user"),
        nullable=False, comment="会话所有者 UUID")
    trip_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True),
        ForeignKey("trip.id", name="fk_chat_session_trip"), nullable=True, comment="首次保存前为空")
    trip_request_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True),
        nullable=True, comment="最近已提交的完整需求快照")
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True, comment="尚未回答的问题")
    latest_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="最近已发布终态的运行 UUID")
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="正在排队或执行的运行 UUID")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"), comment="UTC 创建时间")
    updated_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=text("CURRENT_TIMESTAMP(6)"), comment="UTC 更新时间")
