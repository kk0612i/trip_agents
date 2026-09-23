"""旅行服务短会话、读取失败与保存占位的离线行为验证。"""

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.errors import CapabilityUnavailableError
from app.schemas.trip_schema import Itinerary, ValidationResult
from app.services.trip_service import TripService


def _trip(version_no: int = 3) -> SimpleNamespace:
    """创建 Repository 实际读取的版本记录形状，不连接数据库。"""
    return SimpleNamespace(
        current_version=SimpleNamespace(
            version_no=version_no,
            itinerary_json={"summary": "长沙两日游", "days": [], "total_cost": 0},
        )
    )


def _factory(session: AsyncMock):
    """为单次读取创建可观察关闭行为的异步上下文工厂。"""
    @asynccontextmanager
    async def create_session():
        try:
            yield session
        finally:
            await session.close()

    return create_session


@pytest.mark.asyncio
async def test_load_returns_independent_snapshot_after_session_closed():
    """读取结果在会话关闭后仍可使用，且不依赖原始 JSON 数据。"""
    record = _trip()
    session = AsyncMock()
    session.scalar.return_value = record

    result = await TripService(_factory(session)).load_current(12)

    session.close.assert_awaited_once()
    session.scalar.assert_awaited_once()
    assert result is not None
    version_no, itinerary = result
    record.current_version.itinerary_json["summary"] = "外部记录发生修改"
    assert version_no == 3
    assert isinstance(itinerary, Itinerary)
    assert itinerary.summary == "长沙两日游"
    # 真实 Repository 的查询仍限定到调用方指定的旅行编号。
    statement = session.scalar.await_args.args[0]
    assert list(statement.compile().params.values()) == [12]


@pytest.mark.asyncio
@pytest.mark.parametrize("record", [None, SimpleNamespace(current_version=None)])
async def test_missing_trip_or_version_returns_none_and_closes_session(record):
    """旅行或当前版本缺失是正常空结果，会话仍正常释放。"""
    session = AsyncMock()
    session.scalar.return_value = record

    assert await TripService(_factory(session)).load_current(12) is None
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_database_failure_propagates_original_error_and_closes_session():
    """数据库失败不能伪装成未找到，原异常向上传播且释放会话。"""
    session = AsyncMock()
    error = SQLAlchemyError("数据库读取失败")
    session.scalar.side_effect = error

    with pytest.raises(SQLAlchemyError) as caught:
        await TripService(_factory(session)).load_current(12)

    assert caught.value is error
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_loads_use_separate_short_sessions():
    """重叠进行的读取各自持有独立会话，结果与释放行为互不影响。"""
    both_started = asyncio.Event()
    sessions: list[AsyncMock] = []
    started = 0

    @asynccontextmanager
    async def create_session():
        nonlocal started
        session = AsyncMock()
        sessions.append(session)
        version_no = len(sessions)

        async def scalar(statement):
            nonlocal started
            started += 1
            if started == 2:
                both_started.set()
            await both_started.wait()
            return _trip(version_no)

        session.scalar.side_effect = scalar
        try:
            yield session
        finally:
            await session.close()

    service = TripService(create_session)
    results = await asyncio.wait_for(
        asyncio.gather(service.load_current(11), service.load_current(22)),
        timeout=3,
    )

    assert len(sessions) == 2
    assert sessions[0] is not sessions[1]
    assert sorted(result[0] for result in results if result is not None) == [1, 2]
    for session in sessions:
        session.scalar.assert_awaited_once()
        session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_placeholder_raises_without_opening_session():
    """保存占位显式报告能力不可用，不创建会话或返回虚假成功编号。"""
    factory = Mock(side_effect=AssertionError("保存占位不得创建数据库会话"))
    service = TripService(factory)

    with pytest.raises(CapabilityUnavailableError) as caught:
        await service.save_version(
            trip_id=None,
            trip_request=None,
            change_request=None,
            itinerary=Itinerary(summary="待保存草稿", days=[], total_cost=0),
            routes=[],
            validation=ValidationResult(passed=True),
        )

    assert caught.value.code == "CAPABILITY_UNAVAILABLE"
    assert caught.value.capability == "行程版本保存"
    factory.assert_not_called()
