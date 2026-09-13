"""异步 MySQL 连接池和会话管理。"""

from collections.abc import AsyncIterator
from functools import lru_cache

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> AsyncEngine:
    """延迟创建连接池；应用关闭时应调用 await get_engine().dispose()。"""
    database_url = get_settings().database_url
    if not database_url or not database_url.strip():
        raise ValueError("请在 .env 中配置 DATABASE_URL")
    if make_url(database_url).drivername != "mysql+aiomysql":
        raise ValueError("DATABASE_URL 必须使用 mysql+aiomysql:// 异步驱动")

    return create_async_engine(
        database_url,
        pool_pre_ping=True,  # 借出连接前检查，避免使用已经断开的连接。
        pool_recycle=1800,
    )


@lru_cache(maxsize=1)
def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """只复用会话工厂，不共享具体的 AsyncSession。"""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    """用于 FastAPI Depends；提交由业务事务负责，退出时释放会话。

    未提交的事务会在会话关闭时回滚。直接运行工作流时，可以使用
    async with get_session_factory()() as session 创建独立会话。
    """
    async with get_session_factory()() as session:
        yield session
