"""应用事实和运行依赖边界的静态回归测试。"""

import ast
from pathlib import Path

from app.schemas.trip_schema import Itinerary, ItineraryDay, ItineraryItem
from app.schemas.place_search_schema import SearchPlaceCandidate
from app.services.validation_service import ValidationService


def test_search_facts_convert_without_inventing_planning_attributes():
    fact = SearchPlaceCandidate(place_id="B01", name="博物馆", category="博物馆",
                                city="长沙", longitude=112.9, latitude=28.2)
    candidate = fact.to_planning_candidate()
    assert candidate.estimated_cost is None
    assert candidate.recommended_duration_minutes is None
    assert candidate.city == fact.city
    assert candidate.longitude == fact.longitude
    assert candidate.source == fact.source
    assert fact.ticket_price is None and fact.indoor is None


def test_unspecified_itinerary_price_is_unknown():
    item = ItineraryItem(item_id="i", place_id="B01", name="景点",
                         start_time="09:00", duration_minutes=60)
    assert item.estimated_cost is None
    itinerary = Itinerary(summary="未知票价", days=[ItineraryDay(day_index=1, items=[item])],
                          total_cost=0)
    validation = ValidationService().validate(itinerary, [], 100)
    assert not validation.passed
    assert validation.issues[0].code == "unknown_cost"


def test_runtime_imports_only_direct_provider_dependencies():
    forbidden = {"mcp", "amap_mcp_server", "langchain_mcp_adapters"}
    for source in Path("app").rglob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split(".")[0] in forbidden for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in forbidden
