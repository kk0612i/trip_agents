"""使用内存 SQLite 验证创建会话的持久化、事务和 HTTP 契约。"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import get_current_user, get_session_service
from app.core.errors import TripHasNoVersionError, TripNotFoundError
from app.main import create_app
from app.models import ChatSession, ItineraryVersion, Trip
from app.schemas.api_schema import SessionCreate, UserView
from tests.unit.services.test_session_list import (
    OTHER_USER_ID, USER_ID, add_trip, store,
)


@pytest.fixture
def creation_store(store):
    """复用独立 SQLite 表，使用真实事务适配异步会话调用形式。

    Args:
        store: 列表测试使用的独立数据库和真实服务夹具。

    Returns:
        真实服务、同步数据库会话与异步适配替身。
    """
    # SQL 及提交回滚由真实 Session 执行，替身只适配 await 协议。
    service, session, async_session = store

    @asynccontextmanager
    async def begin():
        """让异常退出触发真实数据库回滚。

        Yields:
            不暴露底层同步事务，业务仅使用异步上下文。
        """
        with session.begin():
            yield

    async_session.begin.side_effect = begin
    async_session.add.side_effect = session.add
    async_session.flush = AsyncMock(side_effect=session.flush)
    async_session.scalar = AsyncMock(side_effect=session.scalar)
    return service, session, async_session


async def test_create_empty_session_commits_without_creating_trip(creation_store):
    """提交后返回完整初始状态，关闭会话后记录仍存在。"""
    service, session, _ = creation_store
    view = await service.create_session(SessionCreate(), USER_ID)
    assert UUID(view.session_id).version == 4
    assert view.trip_id is view.current_version_no is view.trip_request is None
    assert view.active_run_id is view.latest_run_id is view.pending_question is None
    assert view.created_at == view.updated_at
    assert view.model_dump(mode="json")["created_at"].endswith("Z")
    # 关闭会话会回滚未提交写入；重新查询确保记录确实已提交。
    session.close()
    row = session.get(ChatSession, view.session_id)
    assert row.user_id == USER_ID
    assert row.trip_request_json is None
    assert session.scalar(select(func.count()).select_from(Trip)) == 0


async def test_create_linked_session_copies_current_snapshot(creation_store):
    """使用当前版本而非旅行旧需求，保留 BIGINT 和独立嵌套快照。"""
    service, session, _ = creation_store
    # 超出 JavaScript 安全整数范围的旅行编号。
    trip_id = 9007199254740993
    add_trip(session, trip_id)
    session.execute(update(ItineraryVersion).values(request_snapshot_json={
        "destination": "长沙", "days": 3, "preferences": ["博物馆"],
    }))
    session.commit()
    view = await service.create_session(SessionCreate(trip_id=str(trip_id)), USER_ID)
    assert view.trip_id == str(trip_id)
    assert view.current_version_no == 3
    assert view.trip_request.destination == "长沙"
    # 读取独立持久化对象，检查业务没有复用版本的 JSON 对象。
    row = session.get(ChatSession, view.session_id)
    version = session.get(ItineraryVersion, trip_id)
    assert row.trip_request_json == version.request_snapshot_json
    row.trip_request_json["preferences"].append("公园")
    assert version.request_snapshot_json["preferences"] == ["博物馆"]
    assert session.scalar(select(func.count()).select_from(Trip)) == 1


@pytest.mark.parametrize("kind", ["missing", "other_user", "no_version", "wrong_version"])
async def test_invalid_trip_never_creates_session(creation_store, kind):
    """归属与版本验证失败时不留下会话记录。"""
    service, session, _ = creation_store
    if kind != "missing":
        add_trip(session, 42, owner=OTHER_USER_ID if kind == "other_user" else USER_ID,
                 version_trip_id=43 if kind == "wrong_version" else None)
        if kind == "no_version":
            session.execute(update(Trip).values(current_version_id=None))
        session.commit()
    # 不存在和他人资源对外使用同一异常，避免泄露存在性。
    error = TripNotFoundError if kind in {"missing", "other_user"} else TripHasNoVersionError
    with pytest.raises(error):
        await service.create_session(SessionCreate(trip_id="42"), USER_ID)
    assert not session.in_transaction()
    assert session.scalar(select(func.count()).select_from(ChatSession)) == 0


@pytest.mark.parametrize("stage", ["flush", "commit"])
async def test_write_failure_rolls_back_real_insert(creation_store, stage):
    """实际 INSERT 后的 flush 或提交失败必须回滚，不能返回成功。"""
    service, session, async_session = creation_store

    def fail_flush():
        """先执行实际写入，再模拟数据库异常。"""
        session.flush()
        raise SQLAlchemyError("write failed")

    def fail_commit(session):
        """模拟提交阶段数据库失败。"""
        raise SQLAlchemyError("write failed")

    if stage == "flush":
        async_session.flush.side_effect = fail_flush
    else:
        event.listen(session, "before_commit", fail_commit)
    with pytest.raises(SQLAlchemyError, match="write failed"):
        await service.create_session(SessionCreate(), USER_ID)
    assert not session.in_transaction()
    assert session.scalar(select(func.count()).select_from(ChatSession)) == 0


async def test_create_http_contract(creation_store, offline_db_resources):
    """HTTP 层验证成功地址、错误映射、可信身份和未登录拒绝。"""
    service, session, async_session = creation_store
    add_trip(session, 42)
    session.execute(update(Trip).values(current_version_id=None))
    session.commit()
    # 覆盖服务及身份依赖；不连接业务数据库或模型服务。
    app = create_app(resources=offline_db_resources)
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: UserView(id=USER_ID, email="test@example.com")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/sessions", json={})
        assert response.status_code == 201
        assert response.headers["location"] == f'/api/v1/sessions/{response.json()["session_id"]}'
        assert response.headers["cache-control"] == "no-store"
        for trip_id, status, code in [("99", 404, "TRIP_NOT_FOUND"), ("42", 409, "TRIP_HAS_NO_VERSION")]:
            response = await client.post("/api/v1/sessions", json={"trip_id": trip_id})
            assert response.status_code == status
            assert response.json()["error"]["code"] == code
            assert response.json()["request_id"] == response.headers["x-request-id"]
        response = await client.post("/api/v1/sessions", json={"user_id": OTHER_USER_ID})
        assert response.status_code == 422
        async_session.add.reset_mock()
        del app.dependency_overrides[get_current_user]
        response = await client.post("/api/v1/sessions", json={})
        assert response.status_code == 401
        async_session.add.assert_not_called()
