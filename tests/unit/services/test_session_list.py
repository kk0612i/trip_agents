"""在内存 SQLite 执行真实查询，验证分页及 HTTP 投影；不连接业务数据库。"""

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import jwt
import pytest
from sqlalchemy import MetaData, create_engine, delete, insert
from sqlalchemy.dialects import mysql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from app.api.deps import get_current_user, get_session_service
from app.core.errors import InvalidCursorError
from app.core.security import decode_access_token
from app.main import create_app
from app.models import Base, ChatSession, ItineraryVersion, Trip
from app.schemas.api_schema import PageQuery, UserView
from app.services.session_cursor import decode_session_cursor, encode_session_cursor
from app.services.session_service import SessionService

# 测试专用身份、签名密钥和含微秒的 UTC 数据库时间。
USER_ID = str(UUID(int=100))
OTHER_USER_ID = str(UUID(int=200))
SECRET = "session-list-test-key-with-at-least-32-bytes"
CREATED_AT = datetime(2026, 9, 24, 8, 0, 0, 123456)


@pytest.fixture
def store() -> Iterator[tuple[SessionService, Session, Mock]]:
    """复制表定义并移除 MySQL 默认表达式，查询仍使用生产 ORM 和 SQL。

    Yields:
        真实服务、同步内存数据库会话及异步 scalars 适配替身。
    """
    # 独立测试元数据，不修改生产 ORM 的 MySQL DDL 或外键声明。
    metadata = MetaData()
    for table in Base.metadata.tables.values():
        table.to_metadata(metadata)
    for table in metadata.tables.values():
        for column in table.columns:
            column.server_default = None
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        for name in ("chat_session", "trip", "itinerary_version"):
            connection.execute(CreateTable(metadata.tables[name]))
    try:
        with Session(engine) as session:
            # 只适配异步调用形式；过滤、排序和关联均交由 SQL 引擎执行。
            async_session = Mock(spec=AsyncSession)
            async_session.scalars = AsyncMock(side_effect=session.scalars)
            yield SessionService(async_session, cursor_secret=SECRET), session, async_session
    finally:
        engine.dispose()


def add_session(
    session: Session, number: int, *, user_id: str = USER_ID,
    created_at: datetime = CREATED_AT, trip_id: int | None = None,
    destination: str | None = None, days: int | None = None,
) -> str:
    """插入已知位置和需求的会话，返回其 UUID。"""
    session_id = str(UUID(int=number))
    session.execute(insert(ChatSession).values(
        session_id=session_id, user_id=user_id, trip_id=trip_id,
        trip_request_json={"destination": destination, "days": days},
        created_at=created_at, updated_at=created_at,
    ))
    return session_id


def add_trip(
    session: Session, trip_id: int, *, owner: str = USER_ID,
    version_trip_id: int | None = None, destination: str = "长沙",
) -> None:
    """插入旅行及其当前版本；允许构造归属错误验证隔离条件。"""
    session.execute(insert(Trip).values(
        id=trip_id, user_id=owner, request_json={"destination": "旧需求"},
        current_version_id=trip_id, created_at=CREATED_AT, updated_at=CREATED_AT,
    ))
    session.execute(insert(ItineraryVersion).values(
        id=trip_id, trip_id=version_trip_id if version_trip_id is not None else trip_id,
        version_no=3, source_run_id=str(UUID(int=trip_id)),
        request_snapshot_json={"destination": destination, "days": 3},
        itinerary_json={}, routes_json=[], content_hash="a" * 64, created_at=CREATED_AT,
    ))


async def test_pages_keep_ties_and_user_scope_across_insert_and_delete(store):
    """同时间记录不重不漏，新记录不混入后续页，游标记录删除后仍可翻页。"""
    service, session, async_session = store
    for number in range(1, 6):
        add_session(session, number)
    add_session(session, 99, user_id=OTHER_USER_ID)
    first = await service.list_sessions(PageQuery(limit=2), USER_ID)
    assert [row.session_id for row in first.items] == [str(UUID(int=5)), str(UUID(int=4))]
    assert decode_session_cursor(first.next_cursor, user_id=USER_ID, secret=SECRET) == (
        CREATED_AT, str(UUID(int=4)),
    )
    add_session(session, 6, created_at=CREATED_AT + timedelta(seconds=1))
    session.execute(delete(ChatSession).where(ChatSession.session_id == str(UUID(int=4))))
    second = await service.list_sessions(PageQuery(limit=2, cursor=first.next_cursor), USER_ID)
    third = await service.list_sessions(PageQuery(limit=2, cursor=second.next_cursor), USER_ID)
    assert [row.session_id for row in second.items + third.items] == [
        str(UUID(int=3)), str(UUID(int=2)), str(UUID(int=1)),
    ]
    assert third.next_cursor is None
    # 无旅行关联时每页只查一次；查询不会提交或关闭外层会话。
    assert async_session.scalars.await_count == 3
    async_session.commit.assert_not_called()
    async_session.close.assert_not_called()
    # 同一 SQL 可编译为 MySQL 参数绑定查询，保留 LIMIT + 1 的边界。
    statement = async_session.scalars.await_args.args[0]
    compiled = statement.compile(dialect=mysql.dialect())
    assert "ORDER BY chat_session.created_at DESC, chat_session.session_id DESC" in str(compiled)
    assert 3 in compiled.params.values()


@pytest.mark.parametrize("count", [0, 1, 2])
async def test_empty_and_exact_size_final_pages_have_no_cursor(store, count):
    """空页、不满页以及恰好满页但无后续记录均不生成游标。"""
    service, session, _ = store
    for number in range(1, count + 1):
        add_session(session, number)
    page = await service.list_sessions(PageQuery(limit=2), USER_ID)
    assert len(page.items) == count
    assert page.next_cursor is None


async def test_summary_uses_formal_snapshot_and_batches_versions(store):
    """标题按契约派生，BIGINT 输出字符串，版本批量查询且时间附加 UTC。"""
    service, session, async_session = store
    trip_id = 9007199254740993
    add_trip(session, trip_id)
    add_session(session, 1, trip_id=trip_id, destination="未保存的修改", days=7)
    add_session(session, 2, trip_id=trip_id)
    add_session(session, 3, destination="苏州", days=2)
    add_session(session, 4, destination="杭州")
    add_session(session, 5)
    page = await service.list_sessions(PageQuery(), USER_ID)
    assert [row.title for row in page.items] == ["新旅行", "杭州之旅", "苏州 2 日游", "长沙 3 日游", "长沙 3 日游"]
    assert page.items[-1].trip_id == str(trip_id)
    assert page.items[-1].current_version_no == 3
    assert page.items[0].current_version_no is None
    assert page.items[0].created_at == CREATED_AT.replace(tzinfo=timezone.utc)
    assert page.model_dump(mode="json")["items"][0]["updated_at"].endswith("Z")
    assert async_session.scalars.await_count == 2


async def test_mismatched_trip_owner_or_version_never_exposes_snapshot(store):
    """错误关联不能读出他人的正式需求或另一旅行的版本。"""
    service, session, _ = store
    add_trip(session, 10, owner=OTHER_USER_ID, destination="私密目的地")
    add_trip(session, 20, version_trip_id=30, destination="错误旅行的目的地")
    add_session(session, 1, trip_id=10)
    add_session(session, 2, trip_id=20)
    page = await service.list_sessions(PageQuery(), USER_ID)
    assert [row.title for row in page.items] == ["新旅行", "新旅行"]
    assert all(row.current_version_no is None for row in page.items)


@pytest.mark.parametrize("kind", [
    "garbage", "signature", "user", "resource", "missing", "time", "uuid", "version", "oversize", "login",
])
async def test_invalid_cursors_rejected_before_sql(store, kind):
    """签名、身份、资源和位置校验失败时不执行任何列表 SQL。"""
    service, _, async_session = store
    cursor = encode_session_cursor(
        user_id=USER_ID, created_at=CREATED_AT, session_id=str(UUID(int=1)), secret=SECRET,
    )
    payload = jwt.decode(cursor, SECRET, algorithms=["HS256"], audience="sessions")
    if kind == "garbage":
        cursor = "not-a-cursor"
    elif kind == "signature":
        cursor = jwt.encode(payload, "different-key-with-at-least-32-bytes", algorithm="HS256")
    elif kind == "oversize":
        cursor = "x" * 2049
    else:
        if kind == "user":
            payload["user_id"] = OTHER_USER_ID
        elif kind == "resource":
            payload["aud"] = "runs"
        elif kind == "missing":
            del payload["session_id"]
        elif kind == "time":
            payload["created_at"] = "2026-09-24T08:00:00"
        elif kind == "uuid":
            payload["session_id"] = "bad-id"
        elif kind == "version":
            payload["version"] = "2"
        elif kind == "login":
            payload = {"sub": USER_ID, "iss": "trip-agents", "iat": 1, "exp": 9999999999}
        cursor = jwt.encode(payload, SECRET, algorithm="HS256")
    with pytest.raises(InvalidCursorError):
        await service.list_sessions(PageQuery(cursor=cursor), USER_ID)
    async_session.scalars.assert_not_awaited()


def test_pagination_cursor_is_not_an_access_token(monkeypatch):
    """即使复用签名密钥，分页游标也不能作为登录凭证。"""
    monkeypatch.setattr("app.core.security.get_settings", lambda: Mock(auth_jwt_secret=SECRET))
    cursor = encode_session_cursor(
        user_id=USER_ID, created_at=CREATED_AT, session_id=str(UUID(int=1)), secret=SECRET,
    )
    assert decode_access_token(cursor) is None


async def test_http_list_success_validation_and_authentication(store, offline_db_resources):
    """通过真实路由、服务及仓库验证成功响应、后续页、422 和未登录 401。"""
    service, session, async_session = store
    add_session(session, 1, destination="长沙", days=3)
    add_session(session, 2)
    app = create_app(resources=offline_db_resources)
    app.dependency_overrides[get_session_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: UserView(id=USER_ID, email="test@example.com")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        first = await client.get("/api/v1/sessions", params={"limit": 1})
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-store"
        assert len(first.json()["items"]) == 1
        second = await client.get("/api/v1/sessions", params={"limit": 1, "cursor": first.json()["next_cursor"]})
        assert second.status_code == 200
        assert second.json()["items"][0]["title"] == "长沙 3 日游"
        assert second.json()["next_cursor"] is None
        async_session.scalars.reset_mock()
        for params in ({"cursor": "bad"}, {"limit": "0"}, {"limit": "101"}, {"limit": "1.0"}):
            response = await client.get("/api/v1/sessions", params=params)
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
            assert response.json()["request_id"] == response.headers["x-request-id"]
        del app.dependency_overrides[get_current_user]
        unauthenticated = await client.get("/api/v1/sessions")
        assert unauthenticated.status_code == 401
        async_session.scalars.assert_not_awaited()


async def test_database_failure_propagates_instead_of_returning_empty_page(store):
    """数据库异常不能伪装成成功空列表。"""
    service, _, async_session = store
    async_session.scalars.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError, match="database unavailable"):
        await service.list_sessions(PageQuery(), USER_ID)
