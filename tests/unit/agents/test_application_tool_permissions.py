"""专业 Agent 只获得应用级工具，权限声明和运行时可见工具保持一致。"""

import pytest

from app.agent.registry import build_default_agent_registry
from app.agent.supervisor.decision import SupervisorAgent
from app.tools.registry import build_default_tool_registry


EXPECTED_TOOLS = {
    "requirement": set(),
    "attraction_search": {"search_attractions", "get_place_detail"},
    "planner": {"calculate_route", "estimate_itinerary_cost"},
    "accommodation": {"search_accommodation", "get_place_detail", "calculate_route"},
    "weather_impact": {"get_weather_forecast"},
    "place_knowledge": set(),
}


@pytest.mark.parametrize("name,expected", EXPECTED_TOOLS.items())
def test_agent_permissions_match_application_tools(name, expected):
    tools = build_default_tool_registry()
    agents = build_default_agent_registry(tool_registry=tools)
    assert agents.allowed_tools(name) == expected
    with tools.scope(name, agents, {}, remaining=3):
        visible = {tool.name for tool in tools.tools_for(name)}
    assert visible == {name for name in expected if tools.get_spec(name).implemented}


def test_default_registry_exposes_only_the_six_application_tools():
    tools = build_default_tool_registry()
    assert {item["name"] for item in tools.descriptions()} == set().union(*EXPECTED_TOOLS.values())
    assert SupervisorAgent.allowed_tools == frozenset()
