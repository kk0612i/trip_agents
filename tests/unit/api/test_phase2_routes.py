"""新 HTTP 骨架的契约与无副作用验证。"""

from uuid import UUID
import httpx
import pytest

from app.core.resources import AppResources
from app.api.deps import get_current_user
from app.main import create_app
from app.schemas.api_schema import UserView

SID = "550e8400-e29b-41d4-a716-446655440000"
RID = "550e8400-e29b-41d4-a716-446655440001"


@pytest.fixture
def application(monkeypatch, offline_db_resources):
    def forbidden(*args, **kwargs):
        raise AssertionError("占位 API 不得初始化外部资源")
    monkeypatch.setattr(AppResources, "llm", property(forbidden))
    monkeypatch.setattr(AppResources, "amap_client", property(forbidden))
    application = create_app(resources=offline_db_resources)
    # 占位行为测试注入可信身份；会话列表及未登录行为由专门测试覆盖。
    application.dependency_overrides[get_current_user] = lambda: UserView(id=SID, email="test@example.com")
    return application


@pytest.mark.parametrize("method,path,payload", [
    ("GET", f"/sessions/{SID}", None),
    ("POST", f"/sessions/{SID}/runs", {"client_request_id": RID, "message": "去长沙"}),
    ("GET", f"/sessions/{SID}/runs", None),
    ("GET", f"/runs/{RID}", None),
    ("GET", f"/runs/{RID}/events", None),
    ("GET", "/trips/42", None),
    ("GET", "/trips/42/versions", None),
    ("GET", "/trips/42/versions/1", None),
])
async def test_all_placeholders_are_501_without_executing_sql(application, method, path, payload):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.request(method, "/api/v1" + path, json=payload)
    assert response.status_code == 501
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert body["request_id"] == response.headers["x-request-id"]
    UUID(body["request_id"])
    assert body["error"]["code"] == "CAPABILITY_UNAVAILABLE"
    assert body["error"]["retryable"] is False
    assert body["error"]["details"] == {}
    if path.startswith(("/sessions", "/runs")):
        assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("path", [
    "/sessions?limit=0", "/sessions?limit=101", "/sessions?limit=true",
    "/sessions?limit=1.0", "/sessions?cursor=%20", "/sessions/not-a-uuid",
    "/trips/0", "/trips/18446744073709551616", "/trips/42/versions/0",
])
async def test_invalid_arguments_rejected_before_placeholder(application, path):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/v1" + path)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_ARGUMENT"
    assert response.json()["request_id"] == response.headers["x-request-id"]


async def test_request_schema_and_auth_validation(application):
    """验证会话和认证入口按公开契约拒绝非法请求。

    Args:
        application: 禁止访问外部资源的 HTTP 骨架应用夹具。
    """
    # 进程内客户端；参数错误应在业务操作前被拒绝。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        # 客户端伪造所有者字段的响应；公开请求不允许指定可信用户身份。
        extra = await client.post("/api/v1/sessions", json={"owner_id": SID})
        assert extra.status_code == 422
        # JSON 语法错误响应，与字段校验失败使用不同错误码。
        invalid_json = await client.post("/api/v1/sessions", content="{", headers={"Content-Type": "application/json"})
        assert invalid_json.status_code == 400
        assert invalid_json.json()["error"]["code"] == "INVALID_JSON"
        # 邮箱和密码均不合法的认证响应；应复用统一参数错误结构。
        auth = await client.post("/api/v1/auth/login", json={"email": "bad", "password": "1"})
        assert auth.status_code == 422
        assert auth.json()["error"]["code"] == "INVALID_ARGUMENT"


@pytest.mark.parametrize("cursor,status", [(f"{RID}:1", 501), (f"{SID}:1", 422),
                                         (f"{RID}:0", 422), ("bad", 422)])
async def test_sse_never_starts_stream_and_checks_replay_id(application, cursor, status):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url="http://test") as client:
        async with client.stream("GET", f"/api/v1/runs/{RID}/events", headers={"Last-Event-ID": cursor}) as response:
            assert response.status_code == status
            assert response.headers["content-type"].startswith("application/json")
            assert b"event:" not in await response.aread()


def test_openapi_contains_contract_routes_and_sse_error(application):
    paths = application.openapi()["paths"]
    expected = {
        "/api/v1/auth/register": {"post"}, "/api/v1/auth/login": {"post"},
        "/api/v1/sessions": {"post", "get"}, "/api/v1/sessions/{session_id}": {"get"},
        "/api/v1/sessions/{session_id}/runs": {"post", "get"},
        "/api/v1/runs/{run_id}": {"get"}, "/api/v1/runs/{run_id}/events": {"get"},
        "/api/v1/trips/{trip_id}": {"get"}, "/api/v1/trips/{trip_id}/versions": {"get"},
        "/api/v1/trips/{trip_id}/versions/{version_no}": {"get"},
    }
    assert set(paths) == set(expected)
    for path, methods in expected.items():
        assert set(paths[path]) == methods
        # 会话接口正逐步接入，不再统一声明 501；运行和旅行仍为占位。
        if path.startswith(("/api/v1/runs", "/api/v1/trips")):
            for method in methods:
                assert "501" in paths[path][method]["responses"]
    assert {"201", "401", "404", "409"} <= set(paths["/api/v1/sessions"]["post"]["responses"])
