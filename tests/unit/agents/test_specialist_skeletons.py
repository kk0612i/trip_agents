"""主管服务边界与未实现专业能力的离线契约回归。"""

from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.agent.accommodation import AccommodationSearchAgent
from app.agent.place_knowledge import PlaceKnowledgeAgent
from app.agent.registry import build_default_agent_registry
from app.agent.supervisor.context import AutonomousGraphContext
from app.agent.supervisor.supervisor_graph import build_autonomous_graph
from app.agent.weather_impact import WeatherImpactAgent
from app.schemas.agent_schema import SupervisorDecision
from app.schemas.trip_schema import Itinerary, TripRequest
from app.services.trip_service import TripService
from tests.fixtures.agents import FakeSupervisorAgent


@pytest.mark.parametrize(
    "agent_type,name,allowed_tools",
    [
        (AccommodationSearchAgent, "accommodation", {
            "search_accommodation", "get_place_detail", "calculate_route",
        }),
        (PlaceKnowledgeAgent, "place_knowledge", set()),
        (WeatherImpactAgent, "weather_impact", {"get_weather_forecast"}),
    ],
)
async def test_named_specialists_keep_unimplemented_contract(agent_type, name, allowed_tools):
    """注册后的真实占位类维持名称和权限，执行不修改输入或捏造数据。"""
    registry = build_default_agent_registry()
    agent = registry.get(name)
    state = {"user_message": "安排旅行", "search_keywords": ["博物馆"]}
    before = deepcopy(state)

    result = await agent.run(state, "执行尚未实现的专业任务")

    assert isinstance(agent, agent_type)
    assert agent.description
    assert agent.allowed_tools == frozenset(allowed_tools)
    assert result.status == "unimplemented"
    assert result.message == f"{name} Agent 尚未实现"
    assert result.data == {}
    assert state == before


async def test_graph_closes_service_session_before_supervisor_runs():
    """使用真实 TripService 接线，证明进入决策阶段时查询短会话已结束。"""
    session = AsyncMock()
    session.scalar.return_value = SimpleNamespace(current_version=SimpleNamespace(
        version_no=3,
        itinerary_json={"summary": "已保存的长沙行程", "days": [], "total_cost": 0},
    ))
    service_events = []

    @asynccontextmanager
    async def session_factory():
        service_events.append("open")
        try:
            yield session
        finally:
            await session.close()
            service_events.append("close")

    class CheckingSupervisor:
        async def decide(self, state):
            assert service_events == ["open", "close"]
            assert state["current_version_no"] == 3
            assert state["current_itinerary"].summary == "已保存的长沙行程"
            return SupervisorDecision(action="ask_user", instruction="请补充调整要求")

    result = await build_autonomous_graph(
        trip_service=TripService(session_factory), supervisor=CheckingSupervisor(),
    ).ainvoke({"trip_id": 12, "user_message": "修改行程"})

    assert result["status"] == "needs_input"
    assert "trip_service" not in result
    session.close.assert_awaited_once()


async def test_real_save_placeholder_fails_without_opening_database_session():
    """保存实际服务占位被执行层记录为失败，不产生版本或打开数据库。"""
    factory = Mock(side_effect=AssertionError("保存占位不得创建数据库会话"))
    service = TripService(factory)
    supervisor = FakeSupervisorAgent([
        {"action": "validate", "reason": "检查草稿"},
        {"action": "save", "reason": "保存已校验草稿"},
    ])
    draft = Itinerary.model_validate({
        "summary": "长沙一日游", "days": [{"day_index": 1, "items": [{
            "item_id": "item-1", "place_id": "poi-1", "name": "离线景点",
            "start_time": "09:00", "duration_minutes": 60, "estimated_cost": 0,
        }], "total_cost": 0}], "total_cost": 0,
    })

    result = await build_autonomous_graph(
        context=AutonomousGraphContext(trip_service=service), supervisor=supervisor,
    ).ainvoke({
        "user_message": "保存长沙一日游", "draft_itinerary": draft,
        "trip_request": TripRequest(destination="长沙", days=1),
    })

    assert result["status"] == "failed"
    assert result["saved_version_no"] is None
    assert result["saved_fingerprint"] is None
    assert "CapabilityUnavailableError" in result["error"]
    assert [step["action"] for step in result["steps"]] == ["validate", "save"]
    factory.assert_not_called()
