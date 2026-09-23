"""异步 MySQL 连接池和会话管理。"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

# 每次调用返回独立工作单元；不允许跨并发运行共享 AsyncSession。
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


def create_engine() -> AsyncEngine:
    """创建调用方拥有的引擎；调用方必须在关闭时 await engine.dispose()。"""
    database_url = get_settings().database_url
    if not database_url or not database_url.strip():
        raise ValueError("请在 .env 中配置 DATABASE_URL")
    if make_url(database_url).drivername != "mysql+aiomysql":
        raise ValueError("DATABASE_URL 必须使用 mysql+aiomysql:// 异步驱动")

    return create_async_engine(
        database_url,
        pool_pre_ping=True,  # 借出连接前检查，避免使用已经断开的连接。
        pool_recycle=1800,
        hide_parameters=True,  # 驱动异常不附带 SQL 参数，避免业务数据进入服务器堆栈。
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """为指定引擎创建短会话工厂；工厂不缓存具体会话。

    Args:
        engine: 调用方拥有的异步引擎，释放时机由应用容器决定。

    Returns:
        每次调用创建独立 AsyncSession 的工厂。
    """
    return async_sessionmaker(engine, expire_on_commit=False)
