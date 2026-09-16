from datetime import time
from unittest.mock import AsyncMock

import pytest

from app.models.schemas import Itinerary, ItineraryDay, ItineraryItem, TripRequest
from app.services.amap_service import AmapService, AmapServiceError


@pytest.mark.asyncio
async def test_search_places_builds_queries_maps_fields_and_deduplicates():
    amap_client = AsyncMock()
    amap_client.search_pois.side_effect = [
        {
            "status": "1",
            "pois": [
                {
                    "id": "B001",
                    "name": "岳麓山",
                    "type": "风景名胜;风景名胜;风景名胜",
                    "address": "登高路58号",
                    "location": "112.937,28.184",
                }
            ],
        },
        {
            "status": "1",
            "pois": [
                {
                    "id": "B001",
                    "name": "岳麓山",
                    "type": "风景名胜",
                    "address": "登高路58号",
                    "location": "112.937,28.184",
                },
                {
                    "id": "B002",
                    "name": "火宫殿",
                    "type": "餐饮服务;中餐厅;特色餐厅",
                    "address": [],
                    "location": "112.976,28.191",
                },
            ],
        },
    ]
    service = AmapService(amap_client)

    candidates = await service.search_places(
        TripRequest(destination="长沙", days=2, preferences=["美食", "美食"])
    )

    assert [candidate.place_id for candidate in candidates] == ["B001", "B002"]
    assert candidates[0].latitude == pytest.approx(28.184)
    assert candidates[0].longitude == pytest.approx(112.937)
    assert candidates[0].recommended_duration_minutes == 180
    assert candidates[1].address is None
    assert candidates[1].category == "特色餐厅"
    assert candidates[1].recommended_duration_minutes == 90
    assert [
        call.kwargs["keywords"] for call in amap_client.search_pois.await_args_list
    ] == [
        "旅游景点",
        "美食",
    ]
    assert all(
        call.kwargs["city"] == "长沙"
        for call in amap_client.search_pois.await_args_list
    )


@pytest.mark.asyncio
async def test_calculate_route_geocodes_unique_places_and_maps_each_leg():
    amap_client = AsyncMock()
    amap_client.geocode.side_effect = [
        {"status": "1", "geocodes": [{"location": "112.1,28.1"}]},
        {"status": "1", "geocodes": [{"location": "112.2,28.2"}]},
        {"status": "1", "geocodes": [{"location": "112.3,28.3"}]},
    ]
    amap_client.calculate_route.side_effect = [
        {"status": "1", "route": {"paths": [{"distance": "1250", "duration": "601"}]}},
        {"status": "1", "route": {"paths": [{"distance": "500", "duration": "120"}]}},
    ]
    service = AmapService(amap_client)
    itinerary = Itinerary(
        summary="长沙一日游",
        days=[
            ItineraryDay(
                day_index=1,
                items=[
                    _item("i1", "岳麓山", "岳麓区"),
                    _item("i2", "橘子洲", "岳麓区"),
                    _item("i3", "杜甫江阁", "天心区"),
                ],
            )
        ],
        total_cost=0,
    )

    routes = await service.calculate_route(itinerary)

    assert [route.model_dump() for route in routes] == [
        {
            "from_item_id": "i1",
            "to_item_id": "i2",
            "distance_km": 1.25,
            "duration_minutes": 11,
            "mode": "walking",
            "provider": "amap",
        },
        {
            "from_item_id": "i2",
            "to_item_id": "i3",
            "distance_km": 0.5,
            "duration_minutes": 2,
            "mode": "walking",
            "provider": "amap",
        },
    ]
    assert amap_client.geocode.await_count == 3
    assert amap_client.calculate_route.await_count == 2


@pytest.mark.asyncio
async def test_calculate_route_reports_place_with_missing_geocode():
    amap_client = AsyncMock()
    amap_client.geocode.side_effect = [
        {"status": "1", "geocodes": []},
        {"status": "1", "geocodes": [{"location": "112.2,28.2"}]},
    ]
    itinerary = Itinerary(
        summary="测试",
        days=[
            ItineraryDay(
                day_index=1,
                items=[_item("i1", "未知地点"), _item("i2", "岳麓山")],
            )
        ],
        total_cost=0,
    )

    with pytest.raises(AmapServiceError, match="未知地点"):
        await AmapService(amap_client).calculate_route(itinerary)

    amap_client.calculate_route.assert_not_awaited()


def _item(item_id: str, name: str, address: str | None = None) -> ItineraryItem:
    return ItineraryItem(
        item_id=item_id,
        place_id=f"place-{item_id}",
        name=name,
        start_time=time(9, 0),
        duration_minutes=60,
        address=address,
    )
