import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.models.schemas import ParsedTripRequest
from app.services import LLMService


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
