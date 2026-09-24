"""用户认证数据库访问骨架；不保留内存用户表。"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.engine import Result
from app.core.errors import CapabilityUnavailableError
from app.models.user import AppUser


class UserRepository:
    """借用工作单元的数据库会话，提交、回滚和关闭由上层负责。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存借用会话；构造时不查询数据库。

        Args:
            session: 上层工作单元提供的异步会话；提交、回滚及关闭由上层负责。
        """
        # 当前工作单元的数据库依赖；不得跨并发请求共享，Repository 不负责释放。
        self.session = session

    async def find_by_email(self, email: str) -> AppUser | None:
        """按规范化邮箱读取用户的数据库查询。

        Args:
            email: 已去除首尾空白并转为小写的邮箱，由认证服务提供。

        Returns:
            实现后返回用户实体，未找到时返回 None；当前占位实现不会返回。

        """
        stmt = select(AppUser).where(AppUser.email == email)
        result: Result = await self.session.execute(stmt)

        return result.scalar_one_or_none()

    async def find_by_id(self, user_id: str) -> AppUser | None:
        """令牌校验后按用户编号读取账号的查询。

        Args:
            user_id: 由服务端验证令牌后取得的用户 UUID，对应用户表主键。

        Returns:
            返回用户实体，未找到时返回 None；
        """
        return await self.session.get(AppUser, user_id)

    async def add(self, user: AppUser) -> None:
        """用户写入；事务提交由上层工作单元负责。

        Args:
            user: 认证服务构建的用户实体，包含 UUID、规范化邮箱、密码盐及摘要。

        Returns:
            返回 None，表示不返回额外结果，不代表事务已经提交；当前不会返回。
        """
        self.session.add(user)

        await self.session.flush()