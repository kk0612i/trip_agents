"""资源所有权、关闭失败及启动失败边界的离线验证。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.core.resources as module
from app.core.resources import AppResources
from app.main import create_app


async def test_unused_resources_do_not_initialize_on_lifespan(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("不应初始化外部资源"))
    monkeypatch.setattr(module, "create_engine", forbidden)
    monkeypatch.setattr(module, "get_llm", forbidden)
    monkeypatch.setattr(module, "AmapClient", forbidden)
    app = create_app()
    async with app.router.lifespan_context(app):
        assert app.state.resources is not None
    forbidden.assert_not_called()


async def test_owned_resources_are_lazy_reused_and_closed_once(monkeypatch):
    engine = SimpleNamespace(dispose=AsyncMock())
    factory = Mock()
    sync_client = SimpleNamespace(close=Mock())
    async_client = SimpleNamespace(aclose=AsyncMock())
    amap_client = SimpleNamespace(aclose=AsyncMock())
    model = object()
    engine_factory = Mock(return_value=engine)
    llm_factory = Mock(return_value=model)
    monkeypatch.setattr(module, "create_engine", engine_factory)
    monkeypatch.setattr(module, "create_session_factory", Mock(return_value=factory))
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=sync_client))
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=async_client))
    monkeypatch.setattr(module, "get_llm", llm_factory)
    monkeypatch.setattr(module, "AmapClient", Mock(return_value=amap_client))
    monkeypatch.setattr("app.core.config.get_settings", lambda: SimpleNamespace(amap_api_key="test"))
    resources = AppResources()
    engine_factory.assert_not_called()
    llm_factory.assert_not_called()
    assert resources.session_factory is resources.session_factory is factory
    assert resources.llm is resources.llm is model
    assert resources.amap_client is resources.amap_client
    assert resources.amap_client is amap_client
    engine_factory.assert_called_once()
    llm_factory.assert_called_once_with(http_client=sync_client, http_async_client=async_client)
    await resources.aclose()
    await resources.aclose()
    engine.dispose.assert_awaited_once()
    async_client.aclose.assert_awaited_once()
    sync_client.close.assert_called_once()
    amap_client.aclose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="已关闭"):
        resources.llm


async def test_external_injections_are_never_closed():
    factory = Mock()
    model = SimpleNamespace(aclose=AsyncMock())
    amap = SimpleNamespace(aclose=AsyncMock())
    resources = AppResources(session_factory=factory, llm=model, amap_client=amap)
    assert resources.session_factory is factory
    assert resources.llm is model
    assert resources.amap_client is amap
    app = create_app(resources=resources)
    async with app.router.lifespan_context(app):
        pass
    factory.assert_not_called()
    model.aclose.assert_not_awaited()
    amap.aclose.assert_not_awaited()


async def test_one_close_failure_does_not_skip_remaining_owned_resources(monkeypatch):
    sync_client = SimpleNamespace(close=Mock())
    async_client = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError("关闭失败")))
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=sync_client))
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=async_client))
    monkeypatch.setattr(module, "get_llm", Mock(return_value=object()))
    resources = AppResources()
    resources.llm
    with pytest.raises(RuntimeError, match="关闭失败"):
        await resources.aclose()
    sync_client.close.assert_called_once()


async def test_model_creation_failure_still_releases_allocated_clients(monkeypatch):
    sync_client = SimpleNamespace(close=Mock())
    async_client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(module.httpx, "Client", Mock(return_value=sync_client))
    monkeypatch.setattr(module.httpx, "AsyncClient", Mock(return_value=async_client))
    monkeypatch.setattr(module, "get_llm", Mock(side_effect=ValueError("模型配置缺失")))
    resources = AppResources()
    with pytest.raises(ValueError, match="模型配置缺失"):
        resources.llm
    await resources.aclose()
    sync_client.close.assert_called_once()
    async_client.aclose.assert_awaited_once()


async def test_startup_log_failure_still_cleans_application_resources(monkeypatch):
    resources = AppResources()
    closed = AsyncMock()
    resources._stack.push_async_callback(closed)
    monkeypatch.setattr("app.main.configure_logger", Mock(side_effect=ValueError("日志初始化失败")))
    shutdown = AsyncMock()
    monkeypatch.setattr("app.main.shutdown_logger", shutdown)
    app = create_app(resources=resources)
    with pytest.raises(ValueError, match="日志初始化失败"):
        async with app.router.lifespan_context(app):
            pytest.fail("不能进入启动失败的应用")
    closed.assert_awaited_once()
    shutdown.assert_awaited_once()
