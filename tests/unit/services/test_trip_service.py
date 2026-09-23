"""旅行服务事务边界、读取失败与保存占位的离线验证。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError
from sqlalchemy import event
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CapabilityUnavailableError
from app.schemas.trip_schema import Itinerary, ValidationResult
from app.services.trip_service import TripService


def _trip(version_no: int = 3) -> SimpleNamespace:
    """创建 Repository 实际读取的版本记录形状，不连接数据库。

    Args:
        version_no: 返回的业务版本号。

    Returns:
        包含当前版本及行程 JSON 的查询记录替身。
    """
    return SimpleNamespace(
        current_version=SimpleNamespace(
            version_no=version_no,
            itinerary_json={"summary": "长沙两日游", "days": [], "total_cost": 0},
        )
    )


async def test_load_commits_transaction_but_leaves_session_to_caller():
    """服务结束读取事务，调用方关闭会话后仍能使用独立快照。"""
    record = _trip()
    async with AsyncSession() as session:
        session.scalar = AsyncMock(return_value=record)
        session.close = AsyncMock(wraps=session.close)
        committed = Mock()
        event.listen(session.sync_session, "after_commit", committed)
        service = TripService(session)

        result = await service.load_current(12)

        committed.assert_called_once()
        assert not session.in_transaction()
        session.close.assert_not_awaited()
        session.scalar.assert_awaited_once()
        statement = session.scalar.await_args.args[0]
        assert list(statement.compile().params.values()) == [12]

    session.close.assert_awaited_once()
    assert result is not None
    version_no, itinerary = result
    record.current_version.itinerary_json["summary"] = "外部记录发生修改"
    assert version_no == 3
    assert isinstance(itinerary, Itinerary)
    assert itinerary.summary == "长沙两日游"


@pytest.mark.parametrize("record", [None, SimpleNamespace(current_version=None)])
async def test_missing_trip_or_version_finishes_transaction(record):
    """旅行或当前版本缺失是正常空结果，读取事务仍正常结束。

    Args:
        record: 模拟旅行不存在或当前版本不存在的记录。
    """
    async with AsyncSession() as session:
        session.scalar = AsyncMock(return_value=record)
        assert await TripService(session).load_current(12) is None
        assert not session.in_transaction()


@pytest.mark.parametrize("failure", ["database", "validation"])
async def test_read_failure_rolls_back_and_allows_next_operation(failure):
    """查询或解析失败时回滚，原异常传播，之后可以重新开始事务。

    Args:
        failure: 数据库异常或行程 Schema 校验异常。
    """
    async with AsyncSession() as session:
        error = SQLAlchemyError("数据库读取失败")
        if failure == "database":
            session.scalar = AsyncMock(side_effect=error)
            error_type = SQLAlchemyError
        else:
            record = _trip()
            record.current_version.itinerary_json = {"days": "invalid"}
            session.scalar = AsyncMock(return_value=record)
            error_type = ValidationError
        rolled_back = Mock()
        event.listen(session.sync_session, "after_rollback", rolled_back)
        service = TripService(session)

        with pytest.raises(error_type) as caught:
            await service.load_current(12)

        if failure == "database":
            assert caught.value is error
        rolled_back.assert_called_once()
        assert not session.in_transaction()
        session.scalar.side_effect = None
        session.scalar.return_value = _trip()
        assert (await service.load_current(12))[0] == 3
        assert not session.in_transaction()


async def test_sequential_loads_reuse_repository_with_separate_transactions():
    """同一服务顺序读取时复用仓库，每次调用结束自己的事务。"""
    async with AsyncSession() as session:
        session.scalar = AsyncMock(side_effect=[_trip(1), _trip(2)])
        service = TripService(session)
        repository = service.trip_repository
        committed = Mock()
        event.listen(session.sync_session, "after_commit", committed)

        assert (await service.load_current(11))[0] == 1
        assert not session.in_transaction()
        assert (await service.load_current(22))[0] == 2
        assert not session.in_transaction()
        assert committed.call_count == 2
        assert service.trip_repository is repository
        assert repository.session is session


async def test_save_placeholder_raises_without_touching_session():
    """保存占位报告能力不可用，不执行 SQL、开启事务或返回虚假成功。"""
    session = Mock()
    service = TripService(session)

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
    assert session.mock_calls == []
