"""Session 服务骨架；正式鉴权、事务及调度待实现。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CapabilityUnavailableError
from app.repository.session_repository import SessionRepository
from app.schemas.api_schema import (Page, PageQuery, SessionCreate, SessionView, SessionSummary)


class SessionService:
    """使用请求级数据库会话组织对话业务，事务由服务管理。

    数据库会话由调用方关闭；服务及仓库不得跨并发任务共享。
    """

    def __init__(self, session: AsyncSession) -> None:
        """保存借用会话，并创建使用同一会话的对话仓库。

        Args:
            session: 当前请求或工作单元的数据库异步会话，由调用方创建和关闭。
        """
        self.session = session
        self.session_repository = SessionRepository(session)

    async def create_session(self, payload: SessionCreate) -> SessionView:
        """会话创建待实现；接入前必须验证身份和资源归属。

        Args:
            payload: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 会话创建尚未接入。
        """
        raise CapabilityUnavailableError("会话创建")

    async def list_sessions(self, query: PageQuery) -> Page[SessionSummary]:
        """会话列表待实现；接入前必须验证身份和资源归属。

        Args:
            query: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 会话列表尚未接入。
        """
        raise CapabilityUnavailableError("会话列表")

    async def get_session(self, session_id: str) -> SessionView:
        """会话读取待实现；接入前必须验证身份和资源归属。

        Args:
            session_id: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 会话读取尚未接入。
        """
        raise CapabilityUnavailableError("会话读取")
