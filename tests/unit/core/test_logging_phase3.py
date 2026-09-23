"""关键日志事件、请求隔离与敏感数据边界的离线验收。"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from langchain_core.runnables import RunnableLambda

from app.agent.supervisor.runtime import AgentRuntime
from app.client.amap_client import AmapClient
from app.core.log import logger, safe_log_identifier
from app.core.resources import AppResources
from app.main import create_app
from app.schemas.agent_schema import SupervisorDecision
from app.services.llm_service import LLMService, LLMInvocationError
from app.tools.registry import build_default_tool_registry
from app.tools.errors import RegistryError


@pytest.fixture
def records():
    messages = []
    sink_id = logger.add(messages.append, format="{message}", enqueue=False)
    try:
        yield messages
    finally:
        logger.remove(sink_id)


def events(records, event):
    return [message.record["extra"] for message in records if message.record["extra"].get("event") == event]


async def test_http_route_template_request_context_and_secrets(records, offline_db_resources):
    app = create_app(resources=offline_db_resources)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        responses = await asyncio.gather(
            client.get("/api/v1/runs/12345678-1234-1234-1234-123456789abc?token=secret-query"),
            client.get("/private-secret-path?token=secret-query"),
        )
    summary = events(records, "http_response_started")
    assert {row["route"] for row in summary} == {"/api/v1/runs/{run_id}", "<unmatched>"}
    assert len({row["request_id"] for row in summary}) == 2
    assert {row["request_id"] for row in summary} == {response.headers["X-Request-Id"] for response in responses}
    assert all(row["duration_ms"] >= 0 for row in summary)
    assert all(secret not in "".join(records) for secret in ("secret-query", "private-secret-path", "123456789abc"))


async def test_auth_logs_unavailable_without_secrets(records, offline_db_resources):
    """验证认证占位只记录能力不可用，不记录成功或泄露请求凭证。

    Args:
        records: 日志夹具收集的 Loguru 消息，包含文本及结构化事件字段。
        offline_db_resources: 不连接真实数据库的请求级会话资源。
    """
    # 独立应用实例，避免其他测试的依赖状态影响认证日志。
    app = create_app(resources=offline_db_resources)
    # 仅用于脱敏断言的测试凭证，不应出现在日志中。
    body = {"email": "private@example.com", "password": "secret-password"}
    # 进程内客户端；覆盖注册和登录两个公开入口。
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 注册响应，用于确认服务没有宣称创建用户成功。
        registered = await client.post("/api/v1/auth/register", json=body)
        # 登录响应，用于确认服务没有宣称身份验证成功。
        logged_in = await client.post("/api/v1/auth/login", json=body)
    assert registered.status_code == logged_in.status_code == 501
    assert not events(records, "auth_completed")
    assert len(events(records, "capability_unavailable")) == 2
    assert all(secret not in "".join(records) for secret in body.values())


async def test_http_unhandled_error_only_records_type(records):
    app = create_app()

    @app.get("/test-failure")
    async def fail():
        raise RuntimeError("secret-provider-error")

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        with pytest.raises(RuntimeError):
            await client.get("/test-failure")
    assert events(records, "http_unhandled_error")[0]["error_type"] == "RuntimeError"
    assert "secret-provider-error" not in "".join(records)
    assert all(message.record["exception"] is None for message in records)


async def test_runtime_action_terminal_and_context_are_correlated(records):
    runtime = AgentRuntime(supervisor=SimpleNamespace(decide=AsyncMock(return_value=SupervisorDecision(
        action="ask_user", instruction="private-user-question"))))
    state = await runtime.initialize({"user_message": "private-user-input"})
    state.update(await runtime.decide_update(state))
    state.update(await runtime.execute_update(state))
    runtime.merge_update(state)
    assert events(records, "agent_action_selected")[0]["run_id"] == state["run_id"]
    assert events(records, "agent_run_finished")[0]["status"] == "needs_input"
    assert "private-user" not in "".join(records)


async def test_tool_success_and_budget_rejection_use_counts_without_payload(records):
    tools = build_default_tool_registry()
    permissions = SimpleNamespace(allowed_tools=lambda name: frozenset({"estimate_itinerary_cost"}))
    with tools.scope("planner", permissions, {"run_id": "safe-run"}, 1):
        await tools.call("estimate_itinerary_cost", {"items": [{"category": "meals", "amount": 1, "description": "secret-description"}]})
        with pytest.raises(RegistryError):
            await tools.call("estimate_itinerary_cost", {"items": []})
    assert events(records, "tool_completed")[0]["call_count"] == 1
    assert events(records, "tool_rejected")[0]["call_count"] == 1
    assert "secret-description" not in "".join(records)


async def test_amap_retry_logs_attempts_and_latency_without_key(records, monkeypatch):
    attempts = 0

    async def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(503 if attempts == 1 else 200, json={"status": "1", "pois": []})

    monkeypatch.setattr("app.client.amap_client.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        await AmapClient("secret-key", http_client=http_client).search_pois(keywords="secret-query")
    assert events(records, "amap_retry")[0]["attempt"] == 1
    assert events(records, "amap_completed")[0]["attempt"] == 2
    assert events(records, "amap_completed")[0]["duration_ms"] >= 0
    assert "secret-key" not in "".join(records) and "secret-query" not in "".join(records)


async def test_llm_failure_logs_no_prompt_or_raw_exception(records):
    def fail(value):
        raise RuntimeError("secret-upstream-body")

    with pytest.raises(LLMInvocationError):
        await LLMService(RunnableLambda(fail)).parse_minimal_request("secret-user-prompt")
    event = events(records, "llm_invocation_completed")[0]
    assert event["status"] == "failed" and event["attempt"] == 1
    assert "secret-" not in "".join(records)


async def test_resource_failure_keeps_exception_private_and_cleans_owned_clients(records, monkeypatch):
    sync_client = SimpleNamespace(close=Mock())
    async_client = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr("app.core.resources.httpx.Client", Mock(return_value=sync_client))
    monkeypatch.setattr("app.core.resources.httpx.AsyncClient", Mock(return_value=async_client))
    monkeypatch.setattr("app.core.resources.get_llm", Mock(side_effect=ValueError("secret-config")))
    resources = AppResources()
    with pytest.raises(ValueError):
        resources.llm
    await resources.aclose()
    assert events(records, "resource_initialization_failed")[0]["error_type"] == "ValueError"
    sync_client.close.assert_called_once()
    async_client.aclose.assert_awaited_once()
    assert "secret-config" not in "".join(records)


@pytest.mark.parametrize("value", ["a\nb", "x" * 129, "\x1b[31m", None])
def test_untrusted_identifiers_cannot_inject_log_lines(value):
    assert safe_log_identifier(value) == "-"
