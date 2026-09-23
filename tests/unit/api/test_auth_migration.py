"""认证数据库骨架的契约与无副作用验证。"""

from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from app.core.errors import CapabilityUnavailableError
from app.main import create_app
from app.repository.auth_repository import UserRepository
from app.schemas.auth_schema import Credentials
from app.services.auth_service import AuthService


@pytest.fixture
def application(offline_db_resources):
    """创建使用请求级离线会话的认证测试应用。

    Args:
        offline_db_resources: 未绑定数据库引擎的会话资源，执行 SQL 会失败。

    Returns:
        可以创建会话但不执行 SQL 的独立 FastAPI 应用。
    """
    return create_app(resources=offline_db_resources)


@pytest.mark.parametrize("operation", ["register", "login"])
async def test_auth_placeholder_returns_501_without_database(application, operation):
    """验证合法认证请求明确报告未实现，且不访问数据库或签发令牌。

    Args:
        application: 禁止访问数据库资源的应用夹具。
        operation: 参数化的认证动作，为 register 或 login。
    """
    # 进程内 ASGI 客户端；请求不经过真实网络，退出上下文后释放客户端。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        # 合法认证请求的实际响应；预期为能力不可用，不是登录或注册成功。
        response = await client.post(f"/api/v1/auth/{operation}", json={
            "email": "  Trip.User@Example.COM  ", "password": "  secret123  ",
        })
    assert response.status_code == 501
    # 服务端错误响应正文，用于核对错误码、请求关联编号及成功凭证缺失。
    body = response.json()
    assert body["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert body["error"]["retryable"] is False
    assert body["request_id"] == response.headers["x-request-id"]
    assert "access_token" not in body and "user" not in body
    assert "501" in application.openapi()["paths"][f"/api/v1/auth/{operation}"]["post"]["responses"]


@pytest.mark.parametrize("operation", ["register", "login"])
@pytest.mark.parametrize("payload", [
    {"email": "bad", "password": "secret123"},
    {"email": "user@example.com", "password": "short"},
    {"email": "user@example.com", "password": "secret123", "owner_id": "forged"},
])
async def test_auth_uses_strict_contract_and_safe_errors(application, operation, payload):
    """验证认证参数严格校验，并确保错误响应不会泄露密码。

    Args:
        application: 禁止访问数据库资源的应用夹具。
        operation: 参数化的认证动作，为 register 或 login。
        payload: 故意违反邮箱、密码或额外字段约束的 HTTP 请求正文。
    """
    # 进程内客户端，仅用于测试请求解析与错误投影。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        # 非法参数的实际响应；应在进入业务方法前被拒绝。
        response = await client.post(f"/api/v1/auth/{operation}", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert payload["password"] not in response.text


async def test_auth_invalid_json_uses_public_error(application):
    """验证认证接口将 JSON 语法错误转换为统一的 400 响应。

    Args:
        application: 禁止访问数据库资源的应用夹具。
    """
    # 进程内客户端，用于发送无法解析的原始请求正文。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        # 非法 JSON 的实际响应；与格式合法但字段无效的 422 区分。
        response = await client.post("/api/v1/auth/login", content="{", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_JSON"


def test_credentials_preserve_password_and_normalize_email():
    """验证邮箱规范化，同时保留作为密码内容的首尾空白。"""
    # 经正式 Schema 校验的凭证；邮箱用于账号匹配，密码不得自动裁剪。
    credentials = Credentials(email="  Trip.User@Example.COM  ", password="  secret123  ")
    assert credentials.email == "trip.user@example.com"
    assert credentials.password == "  secret123  "


async def test_token_placeholder_cannot_accept_identity():
    """验证认证占位方法不会把未经验证的令牌当作可信身份。"""
    # 会话哨兵；占位服务不得执行 SQL 或开启事务。
    session = Mock()
    with pytest.raises(CapabilityUnavailableError):
        await AuthService(session).authenticate("unverified-token")
    assert session.mock_calls == []


async def test_repository_placeholder_cannot_read_or_write():
    """验证 Repository 占位入口不会读写数据库或提交事务。"""
    # 借用会话的可观察替身；mock_calls 用于确认没有数据库操作。
    session = Mock()
    # 持有会话替身的 Repository，测试其查询和写入边界。
    repository = UserRepository(session)
    # 按编号读取与新增仍为占位；按邮箱查询已有实现，单独验证。
    with pytest.raises(CapabilityUnavailableError):
        await repository.find_by_id("unknown")
    from app.models.user import AppUser
    with pytest.raises(CapabilityUnavailableError):
        await repository.add(AppUser(id="unknown", email="user@example.com"))
    assert session.mock_calls == []


@pytest.mark.parametrize("found", [False, True])
async def test_email_lookup_filters_without_committing(found):
    """按邮箱查询返回单个结果或空值，不提交、回滚或关闭借用的会话。

    Args:
        found: 模拟数据库是否存在对应邮箱的用户。
    """
    user = object() if found else None
    result = Mock()
    result.scalar_one_or_none.return_value = user
    session = Mock()
    session.execute = AsyncMock(return_value=result)

    assert await UserRepository(session).find_by_email("user@example.com") is user

    session.execute.assert_awaited_once()
    statement = session.execute.await_args.args[0]
    assert list(statement.compile().params.values()) == ["user@example.com"]
    result.scalar_one_or_none.assert_called_once_with()
    session.commit.assert_not_called()
    session.rollback.assert_not_called()
    session.close.assert_not_called()
