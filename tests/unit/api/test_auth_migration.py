"""认证数据库骨架的契约与无副作用验证。"""

from unittest.mock import Mock

import httpx
import pytest

from app.core.errors import CapabilityUnavailableError
from app.core.resources import AppResources
from app.main import create_app
from app.repository.auth_repository import UserRepository
from app.schemas.auth_schema import Credentials
from app.services.auth_service import AuthService


@pytest.fixture
def application(monkeypatch):
    """创建禁止访问数据库资源的认证测试应用。

    Args:
        monkeypatch: pytest 提供的临时替换工具，测试结束后自动恢复资源属性。

    Returns:
        使用数据库访问哨兵的独立 FastAPI 应用。
    """
    def forbidden(*args, **kwargs):
        """拦截数据库资源访问，使提前初始化依赖的行为直接导致测试失败。

        Args:
            args: 属性访问传入的实例等位置参数，不参与业务处理。
            kwargs: 兼容替身调用的关键字参数，不参与业务处理。

        Raises:
            AssertionError: 数据库资源被访问时始终抛出。
        """
        raise AssertionError("认证骨架不得访问数据库资源")
    monkeypatch.setattr(AppResources, "session_factory", property(forbidden))
    return create_app()


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
    # 会话工厂哨兵；任何调用都表示占位服务提前打开了数据库会话。
    factory = Mock(side_effect=AssertionError("不得打开数据库会话"))
    with pytest.raises(CapabilityUnavailableError):
        await AuthService(factory).authenticate("unverified-token")
    factory.assert_not_called()


async def test_repository_placeholder_cannot_read_or_write():
    """验证 Repository 占位入口不会读写数据库或提交事务。"""
    # 借用会话的可观察替身；mock_calls 用于确认没有数据库操作。
    session = Mock()
    # 持有会话替身的 Repository，测试其查询和写入边界。
    repository = UserRepository(session)
    # 两个查询入口的待执行协程，都应以能力不可用结束，不产生用户结果。
    for call in (repository.find_by_email("user@example.com"), repository.find_by_id("unknown")):
        with pytest.raises(CapabilityUnavailableError):
            await call
    from app.models.user import AppUser
    with pytest.raises(CapabilityUnavailableError):
        await repository.add(AppUser(id="unknown", email="user@example.com"))
    assert session.mock_calls == []
