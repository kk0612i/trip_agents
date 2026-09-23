from datetime import time
from unittest.mock import AsyncMock

import pytest

from app.schemas.trip_schema import Itinerary, ItineraryDay, ItineraryItem, TripRequest
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
    assert candidates[0].recommended_duration_minutes is None
    assert candidates[0].estimated_cost is None
    assert candidates[1].address is None
    assert candidates[1].category == "特色餐厅"
    assert candidates[1].recommended_duration_minutes is None
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


@pytest.mark.asyncio
async def test_query_search_preserves_only_facts_and_filters_other_city():
    client = AsyncMock()
    poi = {"id": "B1", "name": "岳麓山", "cityname": "长沙市", "type": "风景名胜", "location": "112.9,28.1"}
    client.search_pois.return_value = {"pois": [poi, poi, {**poi, "id": "B2", "cityname": "武汉市"}, {"name": "无编号"}]}
    results = await AmapService(client).search_places_by_query(keyword="岳麓山", city="长沙", limit=4)
    client.search_pois.assert_awaited_once_with(keywords="岳麓山", city="长沙", offset=4)
    assert len(results) == 1
    assert results[0].city == "长沙市"
    assert results[0].ticket_price is None
    assert results[0].opening_hours is None
    assert results[0].indoor is None
    assert results[0].to_planning_candidate().recommended_duration_minutes is None


@pytest.mark.asyncio
async def test_search_does_not_drop_poi_with_missing_coordinates_or_invent_city():
    client = AsyncMock()
    client.search_pois.return_value = {"pois": [{"id": "B1", "name": "地点", "location": [], "cityname": []}]}
    results = await AmapService(client).search_places_by_query(keyword="地点", city="长沙")
    assert len(results) == 1
    assert results[0].longitude is None
    assert results[0].latitude is None
    assert results[0].city is None


@pytest.mark.asyncio
async def test_place_detail_keeps_unknowns_null():
    client = AsyncMock()
    client.get_poi_detail.return_value = {"pois": [{"id": "B1", "name": "景点", "cityname": "长沙市", "location": "112,28"}]}
    detail = await AmapService(client).get_place_detail(place_id="B1")
    client.get_poi_detail.assert_awaited_once_with(poi_id="B1")
    assert detail["longitude"] == 112
    assert detail["ticket_price"] is None
    assert detail["opening_hours"] is None
    assert detail["indoor"] is None


@pytest.mark.asyncio
async def test_place_detail_rejects_mismatching_provider_id():
    client = AsyncMock()
    client.get_poi_detail.return_value = {"pois": [{"id": "B2", "name": "其他地点"}]}
    with pytest.raises(AmapServiceError, match="不一致"):
        await AmapService(client).get_place_detail(place_id="B1")


@pytest.mark.asyncio
async def test_route_between_uses_existing_coordinates_without_geocoding():
    client = AsyncMock()
    client.calculate_route.return_value = {"route": {"paths": [{"distance": "1200", "duration": "121"}]}}
    route = await AmapService(client).calculate_route_between(
        origin={"name": "起点", "longitude": 112, "latitude": 28},
        destination={"name": "终点", "longitude": 113, "latitude": 29}, mode="driving", city="长沙",
    )
    client.geocode.assert_not_awaited()
    client.calculate_route.assert_awaited_once_with(origin="112.0,28.0", destination="113.0,29.0", mode="driving")
    assert route["distance_km"] == 1.2
    assert route["duration_minutes"] == 3


@pytest.mark.asyncio
async def test_route_between_geocodes_only_missing_coordinates_in_requested_city():
    client = AsyncMock()
    client.geocode.return_value = {"geocodes": [{"location": "113,29"}]}
    client.calculate_route.return_value = {"route": {"paths": [{"distance": "1200", "duration": "121"}]}}
    await AmapService(client).calculate_route_between(
        origin={"name": "起点", "longitude": 112, "latitude": 28},
        destination={"name": "终点", "address": "地址"}, city="长沙",
    )
    client.geocode.assert_awaited_once_with(address="地址 终点", city="长沙")


@pytest.mark.asyncio
async def test_weather_conversion_preserves_provider_fact_fields():
    client = AsyncMock()
    client.get_weather.return_value = {"forecasts": [{"city": "长沙市", "reporttime": "2026-09-21 11:00:00", "casts": [{"date": "2026-09-21", "dayweather": "晴", "daytemp": "30", "nightwind": []}]}]}
    weather = await AmapService(client).get_weather(city="长沙")
    client.get_weather.assert_awaited_once_with(city="长沙")
    assert weather["forecasts"][0]["report_time"] == "2026-09-21 11:00:00"
    assert weather["forecasts"][0]["casts"][0]["dayweather"] == "晴"
    assert weather["forecasts"][0]["casts"][0]["nightwind"] is None


@pytest.mark.asyncio
async def test_empty_weather_is_unavailable_instead_of_success():
    client = AsyncMock()
    client.get_weather.return_value = {"forecasts": []}
    with pytest.raises(AmapServiceError, match="没有返回可用预报"):
        await AmapService(client).get_weather(city="长沙")
