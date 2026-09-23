"""Session 数据访问骨架；尚未访问数据或提交事务。"""

from app.core.errors import CapabilityUnavailableError
from app.schemas.api_schema import (Page, PageQuery, SessionCreate, SessionView, SessionSummary)
from sqlalchemy.ext.asyncio import AsyncSession


class SessionRepository:
    """接收工作单元拥有的会话，未来在此实现存储查询。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存借用会话；关闭、提交与回滚由调用方负责。

        Args:
            session: 外层工作单元提供的异步会话；本对象不负责关闭或提交。
        """
        self.session = session

    async def create_session(self, user_id: str, payload: SessionCreate) -> SessionView:
        """会话创建待实现；user_id 必须来自服务端验证后的身份。

        会话归属、幂等检查和数据库事务尚未接入，当前始终报告不可用。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            payload: 已校验的业务请求，不包含可信的服务端身份。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("会话创建")

    async def list_sessions(self, user_id: str, query: PageQuery) -> Page[SessionSummary]:
        """会话列表待实现；user_id 必须来自服务端验证后的身份。

        会话归属、幂等检查和数据库事务尚未接入，当前始终报告不可用。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("会话列表")

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
