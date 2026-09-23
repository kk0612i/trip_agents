"""Run 服务骨架；正式鉴权、事务及调度待实现。"""

from collections.abc import AsyncIterator

from app.core.errors import CapabilityUnavailableError
from app.schemas.api_schema import (Page, PageQuery, RunSubmission, RunReceipt, RunView, SSEEvent)


class RunService:
    """声明公开业务能力，不返回虚假数据或启动后台任务。"""

    async def submit_run(self, session_id: str, payload: RunSubmission) -> RunReceipt:
        """运行受理待实现；接入前必须验证身份和资源归属。

        Args:
            session_id: 已由入口校验的请求参数；不代表已获访问授权。
            payload: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 运行受理尚未接入。
        """
        raise CapabilityUnavailableError("运行受理")

    async def list_runs(self, session_id: str, query: PageQuery) -> Page[RunView]:
        """运行历史待实现；接入前必须验证身份和资源归属。

        Args:
            session_id: 已由入口校验的请求参数；不代表已获访问授权。
            query: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 运行历史尚未接入。
        """
        raise CapabilityUnavailableError("运行历史")

    async def get_run(self, run_id: str) -> RunView:
        """运行读取待实现；接入前必须验证身份和资源归属。

        Args:
            run_id: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 运行读取尚未接入。
        """
        raise CapabilityUnavailableError("运行读取")

    async def subscribe_events(self, run_id: str, after_event_id: str | None) -> AsyncIterator[SSEEvent]:
        """运行事件订阅待实现；接入前必须验证身份和资源归属。

        Args:
            run_id: 已由入口校验的请求参数；不代表已获访问授权。
            after_event_id: 已由入口校验的请求参数；不代表已获访问授权。

        Raises:
            CapabilityUnavailableError: 运行事件订阅尚未接入。
        """
        raise CapabilityUnavailableError("运行事件订阅")
