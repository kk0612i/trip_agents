"""注册表到 HTTP 的整条调用链，HTTP 仅使用 MockTransport。"""

import httpx

from app.agent.registry import build_default_agent_registry
from app.client.amap_client import AmapClient
from app.services.amap_service import AmapService
from app.tools.registry import build_default_tool_registry


async def test_application_tools_reach_direct_web_endpoints():
    requests = []

    def respond(request):
        requests.append(request)
        path = request.url.path
        payload = {"status": "1"}
        if path in {"/v3/place/text", "/v3/place/detail"}:
            payload["pois"] = [{"id": "B01", "name": "博物馆", "type": "文化;博物馆",
                                "cityname": "长沙市", "location": "112.9,28.2"}]
        elif path == "/v3/geocode/geo":
            payload["geocodes"] = [{"location": "113.0,28.3"}]
        elif path == "/v3/weather/weatherInfo":
            payload["forecasts"] = [{"city": "长沙市", "casts": [
                {"date": "2026-09-21", "dayweather": "晴"}]}]
        else:
            payload["route"] = {"paths": [{"distance": "1200", "duration": "601"}]}
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        service = AmapService(AmapClient("offline-secret", http_client=http,
                                         requests_per_second=100))
        tools = build_default_tool_registry(amap_service=service)
        agents = build_default_agent_registry(tool_registry=tools)
        state = {"trip_request": {"destination": "长沙", "start_date": "2026-09-21"}}
        with tools.scope("attraction_search", agents, state, 2) as ledger:
            search = await tools.call("search_attractions", {"keyword": "博物馆", "city": "北京"})
            detail = await tools.call("get_place_detail", {"place_id": "B01"})
            assert search.status == detail.status == "completed"
            assert detail.data["ticket_price"] is None
            assert ledger.calls == 2
        with tools.scope("planner", agents, state, 1):
            route = await tools.call("calculate_route", {
                "origin": {"name": "博物馆", "longitude": 112.9, "latitude": 28.2},
                "destination": {"name": "公园"}, "mode": "walking"})
            assert route.data["distance_km"] == 1.2
            assert route.data["duration_minutes"] == 11
            assert "longitude" not in route.data["origin"]
        with tools.scope("weather_impact", agents, state, 1):
            weather = await tools.call("get_weather_forecast", {"city": "北京"})
            assert weather.status == "completed"
            assert weather.data["city"] == "长沙"
        with tools.scope("accommodation", agents, state, 1):
            hotels = await tools.call("search_accommodation", {})
            assert hotels.data["candidates"][0]["price_status"] == "unknown"

    assert [request.url.path for request in requests] == [
        "/v3/place/text", "/v3/place/detail", "/v3/geocode/geo",
        "/v3/direction/walking", "/v3/weather/weatherInfo", "/v3/place/text",
    ]
    assert requests[0].url.params["city"] == "长沙"
    assert requests[2].url.params["city"] == "长沙"
    assert requests[4].url.params["city"] == "长沙"
