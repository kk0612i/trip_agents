import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from app.schemas.trip_schema import Itinerary, TripRequest
from app.schemas.requirement_schema import MinimalParsedRequest
from app.schemas.agent_schema import SupervisorDecision
from app.services import LLMInvocationError, LLMOutputParseError, LLMService


@pytest.mark.parametrize(("message", "payload"), [
    ("从上海去长沙玩三天，预算2000，喜欢美食", {
        "intent": "create", "trip_request": {"destination": "长沙", "origin": "上海",
        "days": 3, "budget": 2000, "preferences": ["美食"]}, "missing_fields": []}),
    ("我想去浏阳玩", {
        "intent": "create", "trip_request": {"destination": "浏阳"},
        "missing_fields": ["days"]}),
    ("删掉橘子洲，增加岳麓山", {
        "intent": "revise", "trip_request": None, "search_keywords": ["岳麓山"]}),
])
async def test_parse_minimal_request_returns_validated_intent_and_patch(message, payload):
    service = LLMService(FakeListChatModel(responses=[json.dumps(payload, ensure_ascii=False)]))

    result = await service.parse_minimal_request(message)

    assert isinstance(result, MinimalParsedRequest)
    assert result == MinimalParsedRequest.model_validate(payload)


@pytest.mark.parametrize("target", ["attraction_search", "planner"])
async def test_supervisor_decision_uses_structured_output(target):
    payload = {"action": "call_agent", "target_agent": target,
               "instruction": "处理专业任务", "reason": "所需能力已注册", "finish": False}
    service = LLMService(FakeListChatModel(responses=[json.dumps(payload, ensure_ascii=False)]))

    result = await service.supervisor_decide({}, [])

    assert isinstance(result, SupervisorDecision)
    assert result == SupervisorDecision.model_validate(payload)


async def test_supervisor_prompt_exposes_agent_dispatch_without_tool_actions():
    prompts = []

    def reply(prompt):
        prompts.append(prompt.to_string())
        return '{"action":"call_agent","target_agent":"planner","instruction":"组合行程"}'

    service = LLMService(RunnableLambda(reply))
    result = await service.supervisor_decide(
        {"user_message": "安排长沙行程"}, [{"name": "planner", "description": "组合行程"}])

    assert result.target_agent == "planner"
    assert "安排长沙行程" in prompts[0]
    assert "组合行程" in prompts[0]
    assert "call_tool" not in prompts[0]
    assert "target_tool" not in prompts[0]
    assert "可用工具" not in prompts[0]


@pytest.mark.parametrize(("operation", "expected"), [
    ("requirement", "旅行请求格式不正确"),
    ("supervisor", "Supervisor 决策格式不正确"),
])
async def test_parse_errors_have_operation_specific_messages(operation, expected):
    service = LLMService(FakeListChatModel(responses=["不是 JSON"]))

    with pytest.raises(LLMOutputParseError, match=expected):
        if operation == "requirement":
            await service.parse_minimal_request("去长沙玩三天")
        else:
            await service.supervisor_decide({}, [])


@pytest.mark.parametrize("operation", ["requirement", "supervisor"])
async def test_invocation_error_is_wrapped_without_exposing_upstream_details(operation):
    def fail(_):
        raise RuntimeError("upstream-key=secret")

    service = LLMService(RunnableLambda(fail))

    with pytest.raises(LLMInvocationError, match="调用 LLM") as info:
        if operation == "requirement":
            await service.parse_minimal_request("去长沙玩三天")
        else:
            await service.supervisor_decide({}, [])
    assert "secret" not in str(info.value)


async def test_requirement_prompt_includes_context_and_supervisor_instruction():
    prompts = []

    def reply(prompt):
        prompts.append(prompt.to_string())
        return '{"intent":"revise","trip_request":{"days":2}}'

    current = Itinerary(summary="已有长沙行程", days=[], total_cost=0)
    service = LLMService(RunnableLambda(reply))
    result = await service.parse_minimal_request(
        "改为两天", TripRequest(destination="长沙", days=3), "create",
        instruction="仅解析修改要求", current_itinerary=current)

    assert result.trip_request.days == 2
    assert "长沙" in prompts[0]
    assert "已有长沙行程" in prompts[0]
    assert "仅解析修改要求" in prompts[0]
    assert "改为两天" in prompts[0]
