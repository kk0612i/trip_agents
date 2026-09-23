"""共享 ORM 基类及 MySQL 表选项；导入时不创建数据库资源。"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def table_options(comment: str) -> dict[str, str]:
    """构造各业务表共用的 MySQL 存储选项。

    Args:
        comment: 数据库表的中文业务说明。

    Returns:
        包含表注释、存储引擎、字符集和排序规则的选项。
    """
    return {
        "comment": comment,
        "mysql_engine": "InnoDB",
        "mysql_charset": "utf8mb4",
        "mysql_collate": "utf8mb4_unicode_ci",
    }
