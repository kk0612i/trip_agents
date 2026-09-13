"""旅行和行程版本的 SQLAlchemy ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.dialects.mysql import BIGINT, INTEGER


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


class Trip(Base):
    """旅行主表。"""

    __tablename__ = "trip"
    __table_args__ = (
        Index("idx_trip_current_version_id", "current_version_id"),
        {"comment": "旅行主表", "mysql_engine": "InnoDB",
         "mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"},
    )

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
        comment="旅行编号",
    )
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
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="创建时间",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        # MySQL 的自动更新时间必须显式写入 DDL 默认值表达式。
        server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"),
        server_onupdate=text("CURRENT_TIMESTAMP"),
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
        DateTime,
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        comment="版本创建时间",
    )

    trip: Mapped[Trip] = relationship(
        back_populates="versions",
        foreign_keys=[trip_id],
    )
