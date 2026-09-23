"""Agent 运行及持久化运行事件的 ORM 映射。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, table_options


class AgentRun(Base):
    """一条用户消息对应一次运行；result_json 只保存公开 RunResult。"""

    __tablename__ = "agent_run"
    __table_args__ = (
        UniqueConstraint("run_id", name="uk_agent_run_run_id"),
        UniqueConstraint("session_id", "client_request_id", name="uk_agent_run_submission"),
        Index("idx_agent_run_session_order", "session_id", "id"),
        Index("idx_agent_run_status_created", "status", "created_at"),
        table_options("消息及 Agent 运行"),
    )
    id: Mapped[int] = mapped_column(BIGINT(unsigned=True), primary_key=True, autoincrement=True, comment="内部提交序号")
    run_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="公开运行 UUID")
    session_id: Mapped[str] = mapped_column(String(36),
        ForeignKey("chat_session.session_id", name="fk_agent_run_session"), nullable=False, comment="所属会话 UUID")
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False, comment="客户端逻辑提交 UUID")
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="规范化请求 SHA-256")
    message: Mapped[str] = mapped_column(Text, nullable=False, comment="用户输入，规范化后 1 至 4000 字符")
    expected_version_no: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True, comment="客户端期望版本")
    base_version_no: Mapped[int | None] = mapped_column(INTEGER(unsigned=True), nullable=True, comment="本轮实际加载的版本")
    status: Mapped[str] = mapped_column(String(20), nullable=False, comment="queued/running/completed/needs_input/failed")
    intent: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="已识别意图，识别前为空")
    response: Mapped[str | None] = mapped_column(Text, nullable=True, comment="面向用户的最终回答")
    pending_question: Mapped[str | None] = mapped_column(Text, nullable=True, comment="本轮追问")
    missing_fields_json: Mapped[list[str]] = mapped_column(JSON, nullable=False, comment="缺失字段列表，初始为空数组")
    result_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, comment="公开 RunResult，初始六个字段均为 null")
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True), nullable=True, comment="公开错误，正常时为空")
    graph_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="内部 Graph 运行编号，不向客户端公开")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"), comment="UTC 受理时间")
    started_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True, comment="UTC 开始时间")
    finished_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True, comment="UTC 终态发布时间")


class RunEvent(Base):
    """持久化 SSE 事件，随运行保留；序号由运行事务串行分配。"""

    __tablename__ = "run_event"
    __table_args__ = (table_options("运行进度事件"),)
    run_id: Mapped[str] = mapped_column(String(36),
        ForeignKey("agent_run.run_id", name="fk_run_event_run"), primary_key=True, comment="公开运行 UUID")
    seq: Mapped[int] = mapped_column(INTEGER(unsigned=True), primary_key=True, autoincrement=False, comment="运行内从 1 递增的事件序号")
    event_type: Mapped[str] = mapped_column(String(24), nullable=False, comment="SSE 命名事件类型")
    data_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, comment="公开事件 data，不包含内部 trace")
    occurred_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"), comment="UTC 事件时间")
