"""Session 服务骨架；正式鉴权、事务及调度待实现。"""

from app.core.errors import CapabilityUnavailableError
from app.schemas.api_schema import (Page, PageQuery, SessionCreate, SessionView, SessionSummary)


class SessionService:
    """声明公开业务能力，不返回虚假数据或启动后台任务。"""

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
