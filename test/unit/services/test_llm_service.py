import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.runnables import RunnableLambda

from app.models.schemas import (
    Itinerary,
    ParsedTripRequest,
    TripChangeRequest,
    TripRequest,
)
from app.services import LLMInvocationError, LLMOutputParseError, LLMService


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_message", "llm_response", "expected_intent"),
    [
        ("我想从上海去长沙玩 3 天，预算 2000 元，主要吃美食", {
            "intent": "create", "trip_request": {"destination": "长沙", "origin": "上海", "start_date": None, "days": 3, "budget": 2000, "traveler_count": 1, "preferences": ["美食"], "constraints": [], "pace": "balanced"}, "change_request": None, "missing_fields": []}, "create"),
        ("我想去浏阳玩", {
            "intent": "create", "trip_request": {"destination": "浏阳", "origin": None, "start_date": None, "days": None, "budget": None, "traveler_count": 1, "preferences": [], "constraints": [], "pace": "balanced"}, "change_request": None, "missing_fields": ["days"]}, "create"),
        ("删掉橘子洲，增加岳麓山", {
            "intent": "revise", "trip_request": None, "change_request": {"raw_text": "删掉橘子洲，增加岳麓山", "remove_places": ["橘子洲"], "add_place_keywords": ["岳麓山"], "budget": None, "preferences_to_add": [], "constraints_to_add": []}, "missing_fields": []}, "revise"),
    ],
)
async def test_parse_request(user_message, llm_response, expected_intent):
    service = LLMService(
        llm=FakeListChatModel(responses=[json.dumps(llm_response, ensure_ascii=False)])
    )

    parsed_request = await service.parse_request(user_message, None)

    assert isinstance(parsed_request, ParsedTripRequest)
    assert parsed_request.intent == expected_intent
    assert parsed_request.model_dump() == llm_response


@pytest.mark.asyncio
async def test_parse_request_raises_output_parse_error_for_invalid_response():
    service = LLMService(llm=FakeListChatModel(responses=["不是 JSON"]))

    with pytest.raises(LLMOutputParseError, match="旅行请求格式不正确"):
        await service.parse_request("去长沙玩三天", None)


@pytest.mark.asyncio
async def test_parse_request_raises_invocation_error_when_llm_fails():
    def fail(_):
        raise RuntimeError("upstream unavailable")

    service = LLMService(llm=RunnableLambda(fail))

    with pytest.raises(LLMInvocationError, match="调用 LLM"):
        await service.parse_request("去长沙玩三天", None)


def _itinerary_payload() -> dict:
    return {
        "summary": "长沙一日游",
        "days": [{"day_index": 1, "date": None, "items": [], "total_cost": 0, "walking_distance_km": 0, "warnings": []}],
        "total_cost": 0,
        "currency": "CNY",
    }


@pytest.mark.asyncio
async def test_build_and_revise_itinerary_use_structured_output():
    payload = json.dumps(_itinerary_payload(), ensure_ascii=False)
    service = LLMService(llm=FakeListChatModel(responses=[payload, payload]))
    request = TripRequest(destination="长沙", days=1)
    itinerary = Itinerary(**_itinerary_payload())

    built = await service.build_itinerary(request, [])
    revised = await service.revise_itinerary(itinerary, TripChangeRequest(raw_text="优化"), [])

    assert isinstance(built, Itinerary)
    assert isinstance(revised, Itinerary)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "expected"),
    [
        ("build", "LLM 返回的行程格式不正确"),
        ("revise", "LLM 返回的修改后行程格式不正确"),
    ],
)
async def test_itinerary_parse_errors_have_operation_specific_messages(method, expected):
    service = LLMService(llm=FakeListChatModel(responses=["不是 JSON"]))
    itinerary = Itinerary(**_itinerary_payload())

    with pytest.raises(LLMOutputParseError, match=expected):
        if method == "build":
            await service.build_itinerary(TripRequest(destination="长沙", days=1), [])
        else:
            await service.revise_itinerary(itinerary, TripChangeRequest(raw_text="优化"), [])
