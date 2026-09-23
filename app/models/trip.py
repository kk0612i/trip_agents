"""旅行及行程版本的 ORM 映射；保留当前版本的循环外键关系。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import BIGINT, DATETIME, INTEGER
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Trip(Base):
    """旅行主表。"""

    __tablename__ = "trip"
    __table_args__ = (
        Index("idx_trip_current_version_id", "current_version_id"),
        Index("idx_trip_user_id", "user_id"),
        {"comment": "旅行主表", "mysql_engine": "InnoDB",
         "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
        comment="旅行编号",
    )
    user_id: Mapped[str] = mapped_column(String(36),
        ForeignKey("app_user.id", name="fk_trip_user"), nullable=False, comment="旅行所有者 UUID")
    request_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="用户旅行需求",
    )
    current_version_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "itinerary_version.id",
            name="fk_trip_current_version",
            ondelete="SET NULL",
            onupdate="CASCADE",
            use_alter=True,
        ),
        nullable=True,
        comment="当前使用的行程版本编号",
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        # MySQL 的自动更新时间必须显式写入 DDL 默认值表达式。
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
        server_onupdate=text("CURRENT_TIMESTAMP(6)"),
        comment="修改时间",
    )

    versions: Mapped[list[ItineraryVersion]] = relationship(
        back_populates="trip",
        foreign_keys="ItineraryVersion.trip_id",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    current_version: Mapped[ItineraryVersion | None] = relationship(
        foreign_keys=[current_version_id],
        post_update=True,
    )


class ItineraryVersion(Base):
    """行程版本表。"""

    __tablename__ = "itinerary_version"
    __table_args__ = (
        UniqueConstraint(
            "trip_id",
            "version_no",
            name="uk_itinerary_version_trip_version",
        ),
        Index("idx_itinerary_version_trip_id", "trip_id"),
        UniqueConstraint("source_run_id", name="uk_itinerary_version_source_run"),
        {"comment": "行程版本表", "mysql_engine": "InnoDB",
         "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
        comment="行程版本编号",
    )
    trip_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey(
            "trip.id",
            name="fk_itinerary_version_trip",
            ondelete="CASCADE",
            onupdate="CASCADE",
        ),
        nullable=False,
        comment="所属旅行编号",
    )
    version_no: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        comment="版本号，例如 1、2、3",
    )
    source_run_id: Mapped[str] = mapped_column(String(36),
        ForeignKey("agent_run.run_id", name="fk_itinerary_version_run"),
        nullable=False, comment="来源公开运行 UUID，一次运行最多保存一个版本")
    routes_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, comment="该版本的路线快照")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="版本内容 SHA-256，用于重复保存比对")
    request_snapshot_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="生成该版本时的需求快照",
    )
    itinerary_json: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        comment="结构化行程内容",
    )
    validation_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
        comment="行程校验结果",
    )
    total_cost: Mapped[Decimal | None] = mapped_column(
        Numeric(precision=12, scale=2),
        nullable=True,
        comment="预计总花费",
    )
    created_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
        comment="版本创建时间",
    )

    trip: Mapped[Trip] = relationship(
        back_populates="versions",
        foreign_keys=[trip_id],
    )
