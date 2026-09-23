"""验证请求级服务的会话隔离、依赖缓存及异常清理。"""

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_auth_service,
    get_run_service,
    get_session_service,
    get_trip_service,
)
from app.core.errors import CapabilityUnavailableError
from app.core.resources import AppResources
from app.main import create_app
from app.services.auth_service import AuthService
from app.services.run_service import RunService
from app.services.session_service import SessionService
from app.services.trip_service import TripService


@pytest.mark.parametrize("fail", [False, True])
async def test_concurrent_requests_isolate_sessions_and_always_close(fail):
    """重叠请求各用独立会话，同一请求复用会话，成功或异常均清理。

    Args:
        fail: 是否在接口内抛出业务异常，以覆盖失败清理路径。
    """
    sessions = []
    services = []
    both_started = asyncio.Event()

    @asynccontextmanager
    async def session_factory():
        """创建无数据库连接的可观察会话。

        Yields:
            当前请求独占的真实 AsyncSession。
        """
        async with AsyncSession() as session:
            session.close = AsyncMock(wraps=session.close)
            sessions.append(session)
            yield session

    factory = Mock(side_effect=session_factory)
    app = create_app(resources=AppResources(session_factory=factory))
    factory.assert_not_called()

    @app.get("/test-dependencies")
    async def inspect_services(
        auth: Annotated[AuthService, Depends(get_auth_service)],
        run: Annotated[RunService, Depends(get_run_service)],
        conversation: Annotated[SessionService, Depends(get_session_service)],
        trip: Annotated[TripService, Depends(get_trip_service)],
        repeated_trip: Annotated[TripService, Depends(get_trip_service)],
    ):
        """检查当前请求的服务装配，并等待另一个请求进入。

        Args:
            auth: 当前请求的认证服务。
            run: 当前请求的运行服务。
            conversation: 当前请求的对话服务。
            trip: 当前请求的旅行服务。
            repeated_trip: 用于验证 FastAPI 依赖缓存的重复注入。

        Returns:
            本次会话的对象标识。

        Raises:
            CapabilityUnavailableError: 测试要求覆盖异常清理时抛出。
        """
        assert trip is repeated_trip
        for service, repository in (
            (auth, auth.user_repo), (run, run.run_repository),
            (conversation, conversation.session_repository), (trip, trip.trip_repository),
        ):
            assert service.session is repository.session is trip.session
        services.append((auth, run, conversation, trip))
        if len(services) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=3)
        trip.session.close.assert_not_awaited()
        if fail:
            raise CapabilityUnavailableError("依赖清理测试")
        return {"session": id(trip.session)}

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.wait_for(asyncio.gather(
            client.get("/test-dependencies"), client.get("/test-dependencies"),
        ), timeout=5)

    assert [response.status_code for response in responses] == ([501, 501] if fail else [200, 200])
    assert factory.call_count == len(sessions) == 2
    assert sessions[0] is not sessions[1]
    for first, second in zip(services[0], services[1]):
        assert first is not second
        assert first.session is not second.session
    for session in sessions:
        session.close.assert_awaited_once()


async def test_explicit_auth_test_override_skips_database_dependency():
    """兼容应用工厂的认证替身注入，不再创建应用级真实服务。"""
    factory = Mock(side_effect=AssertionError("替身不应创建数据库会话"))
    service = Mock(spec=AuthService)
    service.login = AsyncMock(side_effect=CapabilityUnavailableError("认证替身"))
    app = create_app(resources=AppResources(session_factory=factory), auth_service=service)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/login", json={
            "email": "user@example.com", "password": "secret123",
        })

    assert response.status_code == 501
    service.login.assert_awaited_once()
    factory.assert_not_called()
