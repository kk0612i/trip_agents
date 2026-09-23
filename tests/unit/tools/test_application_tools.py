"""应用工具只使用离线服务夹具，验证权限、证据与金额边界。"""

from unittest.mock import AsyncMock
import traceback

import pytest

from app.client.amap_client import AmapClientError
from app.schemas.agent_schema import ActionResult
from app.schemas.place_search_schema import SearchPlaceCandidate
from app.tools.errors import RegistryError, ToolExecutionError
from app.tools.registry import ToolRegistry, ToolSpec, build_default_tool_registry
from app.tools.amap import search_attractions


PERMISSIONS = {
    "requirement": frozenset(),
    "attraction_search": frozenset({"search_attractions", "get_place_detail"}),
    "planner": frozenset({"calculate_route", "estimate_itinerary_cost"}),
    "accommodation": frozenset({"search_accommodation", "get_place_detail", "calculate_route"}),
    "weather_impact": frozenset({"get_weather_forecast"}),
    "place_knowledge": frozenset(),
    "SupervisorAgent": frozenset(),
}


class Permissions:
    def allowed_tools(self, name):
        return PERMISSIONS[name]


@pytest.fixture
def amap():
    service = AsyncMock()
    poi = SearchPlaceCandidate(place_id="poi-1", name="岳麓山", category="风景名胜",
                               city="长沙", longitude=112.94, latitude=28.18)
    service.search_places_by_query.return_value = [poi, poi]
    service.get_place_detail.return_value = {
        **poi.model_dump(mode="json"), "ticket_price": None,
        "opening_hours": None, "indoor": None,
    }
    service.calculate_route_between.return_value = {
        "distance_km": 1.2, "duration_minutes": 18,
    }
    service.get_weather.return_value = {"forecasts": [{"city": "长沙", "casts": []}]}
    return service


def state(**request):
    return {"trip_request": {"destination": "长沙", **request}}


async def test_search_uses_request_city_deduplicates_and_records_trace(amap):
    tools = build_default_tool_registry(amap_service=amap)
    with tools.scope("attraction_search", Permissions(), state(), 3) as context:
        result = await tools.call("search_attractions", {"keyword": "山", "city": "北京"})
        assert result.status == "completed"
        assert result.data["city"] == "长沙"
        assert len(result.data["candidates"]) == 1
        assert result.data["source"] == "amap" and result.data["fetched_at"]
        assert context.calls == 1
        assert context.traces[0]["arguments"]["city"] == "长沙"
        assert "poi-1" in context.place_evidence
    amap.search_places_by_query.assert_awaited_once_with(keyword="山", city="长沙", limit=10)


async def test_details_require_current_scope_search_and_unknown_fields_are_null(amap):
    tools = build_default_tool_registry(amap_service=amap)
    with tools.scope("attraction_search", Permissions(), state(), 5):
        denied = await tools.call("get_place_detail", {"place_id": "poi-1"})
        assert denied.status == "failed"
        amap.get_place_detail.assert_not_awaited()
        await tools.call("search_attractions", {"keyword": "山"})
        result = await tools.call("get_place_detail", {"place_id": "poi-1"})
        assert result.status == "completed"
        assert [result.data[field] for field in ("ticket_price", "opening_hours", "indoor")] == [None] * 3
    # 图状态中的候选不构成本轮搜索证据，不能跨 Agent 运行复用。
    with tools.scope("attraction_search", Permissions(), {**state(), "candidates": [{"place_id": "poi-1"}]}, 2):
        denied = await tools.call("get_place_detail", {"place_id": "poi-1"})
        assert denied.status == "failed"
    assert amap.get_place_detail.await_count == 1


async def test_route_delegates_to_domain_service_and_hides_coordinates(amap):
    tools = build_default_tool_registry(amap_service=amap)
    origin = {"place_id": "a", "name": "甲", "longitude": 112.9, "latitude": 28.1}
    destination = {"name": "乙", "address": "路 1 号"}
    with tools.scope("planner", Permissions(), state(), 2):
        result = await tools.call("calculate_route", {"origin": origin, "destination": destination})
    assert result.data["distance_km"] == 1.2
    assert result.data["duration_minutes"] == 18
    assert "longitude" not in result.data["origin"]
    args = amap.calculate_route_between.await_args.kwargs
    assert args["origin"].longitude == 112.9
    assert args["destination"].address == "路 1 号"
    assert args["city"] == "长沙" and args["mode"] == "walking"


async def test_unknown_cost_is_not_zero_and_incomplete_cost_cannot_pass_budget():
    tools = build_default_tool_registry()
    with tools.scope("planner", Permissions(), state(budget=100), 2):
        result = await tools.call("estimate_itinerary_cost", {"items": [
            {"category": "tickets", "amount": None, "description": "未知门票"},
            {"category": "meals", "amount": 20.1},
            {"category": "meals", "amount": 0.2},
            {"category": "transportation", "amount": 0},
        ]})
    assert result.data["known_total"] == 20.3
    assert result.data["breakdown"]["tickets"] is None
    assert result.data["breakdown"]["transportation"] == 0
    assert result.data["within_budget"] is None
    assert {item["category"] for item in result.data["unknown_items"]} == {"tickets", "accommodation"}


async def test_complete_known_cost_uses_trip_budget_and_explicit_free_zero():
    tools = build_default_tool_registry()
    with tools.scope("planner", Permissions(), state(budget=19), 2):
        result = await tools.call("estimate_itinerary_cost", {"budget": 999, "items": [
            {"category": "tickets", "amount": 0}, {"category": "meals", "amount": 20},
            {"category": "transportation", "amount": 0}, {"category": "accommodation", "amount": 0},
        ]})
    assert result.data["known_total"] == 20
    assert result.data["unknown_items"] == []
    assert result.data["budget"] == 19
    assert result.data["within_budget"] is False


async def test_accommodation_is_only_existence_evidence(amap):
    tools = build_default_tool_registry(amap_service=amap)
    with tools.scope("accommodation", Permissions(), state(), 3):
        result = await tools.call("search_accommodation", {"city": "北京"})
        detail = await tools.call("get_place_detail", {"place_id": "poi-1"})
    assert result.data["city"] == "长沙"
    assert result.data["candidates"][0]["price_status"] == "unknown"
    assert result.data["warnings"]
    assert detail.status == "completed"


async def test_weather_requires_date_and_uses_request_city(amap):
    tools = build_default_tool_registry(amap_service=amap)
    with tools.scope("weather_impact", Permissions(), state(), 2):
        result = await tools.call("get_weather_forecast", {})
    assert result.status == "unimplemented"
    amap.get_weather.assert_not_awaited()
    with tools.scope("weather_impact", Permissions(), state(start_date="2026-09-21"), 2):
        result = await tools.call("get_weather_forecast", {"city": "北京"})
    assert result.status == "completed" and result.data["city"] == "长沙"
    amap.get_weather.assert_awaited_once_with(city="长沙")


async def test_unavailable_weather_and_failed_search_redact_service_errors(amap):
    tools = build_default_tool_registry(amap_service=amap)
    amap.get_weather.side_effect = AmapClientError("https://upstream/?key=secret")
    with tools.scope("weather_impact", Permissions(), state(start_date="2026-09-21"), 2) as context:
        result = await tools.call("get_weather_forecast", {})
        assert result.status == "unavailable"
        assert "secret" not in str(context.traces)
    amap.search_places_by_query.side_effect = RuntimeError("key=secret")
    with tools.scope("attraction_search", Permissions(), state(), 2) as context:
        with pytest.raises(ToolExecutionError) as exc:
            await tools.call("search_attractions", {"keyword": "山"})
        assert "secret" not in str(exc.value)
        assert "key=secret" not in "".join(traceback.format_exception(exc.value))
        assert "secret" not in str(context.traces)
        assert context.calls == 1


async def test_permissions_budget_and_unimplemented_tools_are_enforced(amap):
    tools = build_default_tool_registry(amap_service=amap)
    with tools.scope("attraction_search", Permissions(), state(), 1) as context:
        await tools.call("search_attractions", {"keyword": "山"})
        with pytest.raises(RegistryError, match="最大工具"):
            await tools.call("search_attractions", {"keyword": "湖"})
        assert context.calls == 1 and context.traces[-1]["status"] == "rejected"
    with tools.scope("planner", Permissions(), state(), 2):
        with pytest.raises(RegistryError, match="无权"):
            await tools.call("search_attractions", {"keyword": "山"})
    pending = ToolRegistry(amap_service=amap)
    pending.register(ToolSpec(search_attractions, frozenset({"attraction_search"}), implemented=False))
    before = amap.search_places_by_query.await_count
    with pending.scope("attraction_search", Permissions(), state(), 2):
        assert pending.tools_for("attraction_search") == []
        result = await pending.call("search_attractions", {"keyword": "山"})
        assert result.status == "unimplemented"
    assert amap.search_places_by_query.await_count == before


def test_registry_exposes_exactly_six_application_tools_with_hidden_runtime():
    tools = build_default_tool_registry()
    descriptions = tools.descriptions()
    assert {item["name"] for item in descriptions} == {
        "search_attractions", "get_place_detail", "calculate_route",
        "estimate_itinerary_cost", "search_accommodation", "get_weather_forecast",
    }
    assert all(item["implemented"] for item in descriptions)
    assert all("runtime" not in item["arguments"]["properties"] for item in descriptions)


def test_cost_business_function_is_reusable_without_runtime_and_does_not_mutate_items():
    """纯业务费用汇总无须构造工具作用域，补齐未知项时不污染调用方列表。"""
    from app.schemas.tool_schema import CostItem
    from app.services.cost_service import summarize_itinerary_cost

    items = [CostItem(category="meals", amount=0.1), CostItem(category="meals", amount=0.2)]
    original = [item.model_dump() for item in items]
    first = summarize_itinerary_cost(items, 0.3, "CNY")
    second = summarize_itinerary_cost(items, 0.3, "CNY")
    assert first == second
    assert [item.model_dump() for item in items] == original
    assert first["known_total"] == 0.3
    assert first["within_budget"] is None
    assert first["breakdown"]["tickets"] is None
    assert {item["category"] for item in first["unknown_items"]} == {
        "transportation", "tickets", "accommodation"}
