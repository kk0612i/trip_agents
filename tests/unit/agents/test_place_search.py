"""应用级搜索/详情工具经过真实 Agent 消息循环，外部服务使用离线替身。"""

from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pydantic import PrivateAttr

from app.agent.place_search.place_search_graph import PlaceSearchAgent
from app.agent.registry import build_default_agent_registry
from app.schemas.place_search_schema import SearchPlaceCandidate
from app.schemas.trip_schema import TripRequest
from app.tools.registry import build_default_tool_registry


class SearchModel(FakeMessagesListChatModel):
    _bound_tools: set[str] = PrivateAttr(default_factory=set)

    def bind_tools(self, tools, **kwargs):
        self._bound_tools.update(tool.name if hasattr(tool, "name") else tool["function"]["name"]
                                 for tool in tools)
        return self


def tool_call(name, arguments, number):
    return AIMessage(content="", tool_calls=[{
        "name": name, "args": arguments, "id": str(number), "type": "tool_call",
    }])


@pytest.mark.asyncio
async def test_search_agent_can_search_and_detail_with_one_shared_budget():
    poi = SearchPlaceCandidate(place_id="poi-1", name="离线博物馆", category="博物馆", city="长沙市")
    service = AsyncMock()
    service.search_places_by_query.return_value = [poi]
    service.get_place_detail.return_value = poi.model_dump()
    tools = build_default_tool_registry(amap_service=service)
    agents = build_default_agent_registry(tool_registry=tools)
    model = SearchModel(responses=[
        tool_call("search_attractions", {"keyword": "博物馆", "city": "北京"}, 1),
        tool_call("get_place_detail", {"place_id": "poi-1"}, 2),
        tool_call("_SearchDecision", {"selected_place_ids": ["poi-1"]}, 3),
    ])
    agent = PlaceSearchAgent(model, tools)
    state = {"trip_request": TripRequest(destination="长沙")}
    with tools.scope(agent.name, agents, state, remaining=3) as context:
        result = await agent.run(state, "搜索博物馆")
    assert result.status == "completed", result.message
    assert context.calls == result.data["tool_call_count"] == 2
    assert result.data["candidates"][0]["ticket_price"] is None
    service.search_places_by_query.assert_awaited_once_with(keyword="博物馆", city="长沙", limit=10)
    assert model._bound_tools == {"search_attractions", "get_place_detail", "_SearchDecision"}


@pytest.mark.asyncio
async def test_search_agent_rejects_selected_id_without_search_evidence():
    service = AsyncMock()
    service.search_places_by_query.return_value = []
    tools = build_default_tool_registry(amap_service=service)
    agents = build_default_agent_registry(tool_registry=tools)
    model = SearchModel(responses=[
        tool_call("search_attractions", {"keyword": "博物馆"}, 1),
        tool_call("_SearchDecision", {"selected_place_ids": ["invented"]}, 2),
    ])
    agent = PlaceSearchAgent(model, tools)
    state = {"trip_request": TripRequest(destination="长沙")}
    with tools.scope(agent.name, agents, state, remaining=3):
        result = await agent.run(state, "搜索博物馆")
    assert result.status == "failed"
    assert "工具结果之外" in result.message


@pytest.mark.asyncio
async def test_search_failure_keeps_new_trace_and_does_not_reuse_older_evidence():
    """拆分证据层后仍只读取本次运行新增记录，并保留实际失败次数。"""
    service = AsyncMock()
    service.search_places_by_query.return_value = [
        SearchPlaceCandidate(place_id="old-poi", name="旧候选", category="博物馆")]
    tools = build_default_tool_registry(amap_service=service)
    agents = build_default_agent_registry(tool_registry=tools)
    model = SearchModel(responses=[
        tool_call("search_attractions", {"keyword": "本轮搜索"}, 1),
        tool_call("_SearchDecision", {"selected_place_ids": ["old-poi"]}, 2),
    ])
    agent = PlaceSearchAgent(model, tools)
    state = {"trip_request": TripRequest(destination="长沙")}
    with tools.scope(agent.name, agents, state, remaining=4) as context:
        await tools.call("search_attractions", {"keyword": "上轮搜索"})
        service.search_places_by_query.side_effect = RuntimeError("provider-secret")
        result = await agent.run(state, "重新搜索")
    assert result.status == "failed"
    assert "高德搜索失败" in result.message
    assert context.calls == 2
    assert result.data["tool_call_count"] == 1
    assert result.data["candidates"] == []
    assert [trace["keyword"] for trace in result.data["tool_calls"]] == ["本轮搜索"]
    assert "provider-secret" not in str(result.model_dump())


@pytest.mark.asyncio
async def test_search_rejects_search_and_final_selection_in_same_batch():
    """最终选择不能引用同一批尚未执行的搜索，拒绝前不消耗工具额度。"""
    service = AsyncMock()
    tools = build_default_tool_registry(amap_service=service)
    agents = build_default_agent_registry(tool_registry=tools)
    search = tool_call("search_attractions", {"keyword": "博物馆"}, 1)
    selection = tool_call("_SearchDecision", {"selected_place_ids": []}, 2)
    model = SearchModel(responses=[AIMessage(content="", tool_calls=search.tool_calls + selection.tool_calls)])
    agent = PlaceSearchAgent(model, tools)
    state = {"trip_request": TripRequest(destination="长沙")}
    with tools.scope(agent.name, agents, state, remaining=3) as context:
        result = await agent.run(state, "搜索")
    assert result.status == "failed"
    assert result.message == "搜索与最终筛选不能同时提交"
    assert context.calls == result.data["tool_call_count"] == 0
    service.search_places_by_query.assert_not_awaited()
