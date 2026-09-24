"""会话数据访问；查询当前用户的会话及关联正式版本，不提交事务。"""
from datetime import datetime

from sqlalchemy import select, or_, and_
from app.core.errors import CapabilityUnavailableError
from app.models import ChatSession
from app.models.trip import ItineraryVersion, Trip
from app.schemas.api_schema import SessionView
from sqlalchemy.ext.asyncio import AsyncSession


class SessionRepository:
    """使用外层工作单元提供的数据库会话执行查询。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存借用会话；关闭、提交与回滚由调用方负责。

        Args:
            session: 外层工作单元提供的异步会话；本对象不负责关闭或提交。
        """
        self.session = session

    async def create_session(self, chat_session: ChatSession) -> None:
        """写入会话，由入口业务事务统一提交。

        Args:
            chat_session: 已由业务层确定所有者、旅行关联及初始状态的会话。
        """
        self.session.add(chat_session)
        await self.session.flush()

    async def get_owned_trip(self, *, user_id: str, trip_id: int) -> Trip | None:
        """按可信用户身份读取旅行，不区分不存在与他人资源。

        Args:
            user_id: 服务端认证的用户编号。
            trip_id: 待关联旅行的数据库主键。

        Returns:
            当前用户的旅行；不存在或不属于当前用户时返回 None。
        """
        # 归属条件直接进入 SQL，避免加载其他用户的旅行数据。
        stmt = select(Trip).where(Trip.id == trip_id, Trip.user_id == user_id)
        return await self.session.scalar(stmt)

    async def list_sessions(
            self,
            user_id: str,
            limit: int,
            after: tuple[datetime, str] | None = None,
    ) -> tuple[list[ChatSession], bool]:
        """按创建时间和 UUID 倒序查询当前用户的会话。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            limit: 已校验的页长，范围为 1 到 100。
            after: 上页最后一条的 UTC 创建时间与编号；None 表示首页。

        Returns:
            当前页会话与是否存在后续页；数据库错误直接向上抛出。
        """
        # 只查询当前登录用户的会话。
        stmt = select(ChatSession).where(ChatSession.user_id == user_id)
        if after is not None:
            last_created_at, last_session_id = after

            # 时间更早，或时间相同但 ID 更小的记录，属于后续页。
            stmt = stmt.where(
                or_(
                    ChatSession.created_at < last_created_at,
                    and_(
                        ChatSession.created_at == last_created_at,
                        ChatSession.session_id < last_session_id,
                    ),
                )
            )

        stmt = stmt.order_by(
            ChatSession.created_at.desc(),
            ChatSession.session_id.desc(),
        ).limit(limit + 1)

        result = await self.session.scalars(stmt)
        rows = list(result.all())

        # 多查一条，用于判断是否还有下一页。
        has_more = len(rows) > limit
        return rows[:limit], has_more

    async def get_current_versions(
        self, *, user_id: str, trip_ids: list[int],
    ) -> dict[int, ItineraryVersion]:
        """批量读取本页关联旅行的当前正式版本，避免逐条查询。

        Args:
            user_id: 服务端认证的用户编号。
            trip_ids: 本页会话关联的旅行编号；为空时不执行 SQL。

        Returns:
            旅行编号到当前版本的映射；无匹配或归属不符时不包含该编号。
        """
        if not trip_ids:
            return {}
        # 同时检查旅行所有者及版本所属旅行，防止错误关联泄露他人需求。
        stmt = select(ItineraryVersion).join(
            Trip,
            and_(Trip.current_version_id == ItineraryVersion.id, Trip.id == ItineraryVersion.trip_id),
        ).where(Trip.user_id == user_id, Trip.id.in_(trip_ids))
        # 一次加载需求快照和版本号，响应转换阶段不触发懒加载。
        result = await self.session.scalars(stmt)
        return {version.trip_id: version for version in result.all()}


    async def get_session(self, user_id: str, session_id: str) -> SessionView | None:
        """会话读取待实现；user_id 必须来自服务端验证后的身份。

        会话归属、幂等检查和数据库事务尚未接入，当前始终报告不可用。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            session_id: 公开会话 UUID；访问前仍需验证归属。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("会话读取")
