"""应用级延迟资源容器，只释放由本容器实际创建的连接。"""

from contextlib import AsyncExitStack

import httpx
from langchain_core.runnables import Runnable

from app.client.amap_client import AmapClient
from app.core.db import SessionFactory, create_engine, create_session_factory
from app.core.llm import get_llm
from app.core.log import log_event


class AppResources:
    """按需创建模型、地图和数据库资源；注入对象始终由调用方管理。"""

    def __init__(
        self, *, session_factory: SessionFactory | None = None,
        llm: Runnable | None = None, amap_client: AmapClient | None = None,
    ) -> None:
        """保存资源或工厂，此时不加载配置、不创建客户端或连接池。

        Args:
            session_factory: 外部拥有的短会话工厂，None 表示首次查询时创建。
            llm: 外部拥有的模型，None 表示首次推理前创建。
            amap_client: 外部拥有的地图客户端，None 表示首次地图调用前创建。
        """
        # 仅保留工厂，不持有某一次查询的会话。
        self._session_factory = session_factory
        self._llm = llm
        self._amap_client = amap_client
        # 只登记自建资源的关闭动作，借用资源不进入栈。
        self._stack = AsyncExitStack()
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("应用资源已关闭")

    @property
    def session_factory(self) -> SessionFactory:
        """返回短会话工厂；只创建自有引擎时登记 dispose 回调。"""
        self._ensure_open()
        if self._session_factory is None:
            try:
                engine = create_engine()
                self._stack.push_async_callback(engine.dispose)
                self._session_factory = create_session_factory(engine)
            except Exception as exc:
                log_event("resource_initialization_failed", level="ERROR", resource="database", error_type=type(exc).__name__)
                raise
            log_event("resource_initialized", resource="database", status="completed")
        return self._session_factory

    @property
    def llm(self) -> Runnable:
        """首次使用时创建模型和两个 HTTP 客户端，并登记自有连接释放。"""
        self._ensure_open()
        if self._llm is None:
            try:
                sync_client = httpx.Client()
                self._stack.callback(sync_client.close)
                async_client = httpx.AsyncClient()
                self._stack.push_async_callback(async_client.aclose)
                self._llm = get_llm(http_client=sync_client, http_async_client=async_client)
            except Exception as exc:
                log_event("resource_initialization_failed", level="ERROR", resource="llm", error_type=type(exc).__name__)
                raise
            log_event("resource_initialized", resource="llm", status="completed")
        return self._llm

    @property
    def amap_client(self) -> AmapClient:
        """首次使用时创建地图客户端；不关闭外部注入的客户端。"""
        self._ensure_open()
        if self._amap_client is None:
            from app.core.config import get_settings

            try:
                client = AmapClient(get_settings().amap_api_key)
                self._stack.push_async_callback(client.aclose)
                self._amap_client = client
            except Exception as exc:
                log_event("resource_initialization_failed", level="ERROR", resource="amap", error_type=type(exc).__name__)
                raise
            log_event("resource_initialized", resource="amap", status="completed")
        return self._amap_client

    async def aclose(self) -> None:
        """逆序释放所有自有资源；单个回调失败也继续其他释放操作。"""
        if not self._closed:
            self._closed = True
            try:
                await self._stack.aclose()
            except Exception as exc:
                log_event("resources_closed", level="ERROR", status="failed", error_type=type(exc).__name__)
                raise
            log_event("resources_closed", status="completed")
