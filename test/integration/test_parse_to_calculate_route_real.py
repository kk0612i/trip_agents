import os
from datetime import time

import pytest

from app.agents.context import TripGraphContext
from app.agents.nodes.request import parse_request
from app.agents.nodes.route import calculate_route
from app.client.amap_client import AmapClient
from app.client.llm_client import get_llm
from app.core.config import get_settings
from app.models.schemas import Itinerary, ItineraryDay, ItineraryItem
from app.services.amap_service import AmapService
from app.services.llm_service import LLMService


class _Runtime:
    def __init__(self, context):
        self.context = context


@pytest.mark.integration
@pytest.mark.asyncio
async def test_parse_request_to_calculate_route_uses_real_services():
    """使用真实 LLM 和高德服务，验证解析结果能驱动路线计算节点。"""
    settings = get_settings()
    llm_service = LLMService(get_llm())
    async with AmapClient(settings.amap_api_key) as amap_client:
        context = TripGraphContext(
            repository=None,
            llm=llm_service,
            amap=AmapService(amap_client),
            validator=None,
        )
        runtime = _Runtime(context)

        parsed = await parse_request(
            {"user_message": "我想去长沙玩两天，喜欢历史景点", "current_itinerary": None},
            runtime,
        )

        assert "error" not in parsed
        request = parsed["trip_request"]
        assert request is not None
        assert request.destination
        assert request.days == 2

        candidates = await context.amap.search_places(request)
        assert candidates
        first, second = candidates[:2]
        itinerary = Itinerary(
            summary="真实环境节点测试",
            total_cost=0,
            days=[
                ItineraryDay(
                    day_index=1,
                    items=[
                        ItineraryItem(
                            item_id="real-1",
                            place_id=first.place_id,
                            name=first.name,
                            address=first.address,
                            start_time=time(9, 0),
                            duration_minutes=60,
                        ),
                        ItineraryItem(
                            item_id="real-2",
                            place_id=second.place_id,
                            name=second.name,
                            address=second.address,
                            start_time=time(11, 0),
                            duration_minutes=60,
                        ),
                    ],
                )
            ],
        )

        routed = await calculate_route({"draft_itinerary": itinerary}, runtime)
        assert len(routed["route_info"]) == 1
        route = routed["route_info"][0]
        assert route.from_item_id == "real-1"
        assert route.to_item_id == "real-2"
        assert route.distance_km >= 0
        assert route.duration_minutes >= 0
