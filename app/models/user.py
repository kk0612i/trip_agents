"""用户账号的 ORM 映射。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import LargeBinary, String, UniqueConstraint, text
from sqlalchemy.dialects.mysql import DATETIME
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, table_options


class AppUser(Base):
    """账号；盐为 16 字节，PBKDF2-SHA256 哈希为 32 字节，迭代 240000 次。"""

    __tablename__ = "app_user"
    __table_args__ = (UniqueConstraint("email", name="uk_app_user_email"), table_options("用户账号"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="用户 UUID")
    email: Mapped[str] = mapped_column(String(254), nullable=False, comment="去首尾空白并转小写的邮箱")
    password_hash: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False, comment="PBKDF2-SHA256 密码哈希")
    password_salt: Mapped[bytes] = mapped_column(LargeBinary(16), nullable=False, comment="随机密码盐")
    created_at: Mapped[datetime] = mapped_column(DATETIME(fsp=6), nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"), comment="UTC 创建时间")
