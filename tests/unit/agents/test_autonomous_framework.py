import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.supervisor.supervisor_graph import build_autonomous_graph
from app.agent.supervisor.context import AutonomousGraphContext
from app.agent.registry import AgentRegistry, build_default_agent_registry
from app.tools.errors import RegistryError
from app.tools.registry import ToolRegistry, ToolSpec, build_default_tool_registry
from tests.fixtures.tools import query_tool
from app.agent.supervisor.runtime import AgentRuntime, RuntimeLimits
from app.agent.requirement import RequirementAgent
from tests.fixtures.agents import FakeSupervisorAgent
from app.agent.supervisor.decision import SupervisorAgent
from app.agent.supervisor.fallback import RuleBasedSupervisorAgent
from app.schemas.agent_schema import ActionResult, SupervisorDecision
from app.schemas.trip_schema import Itinerary, TripRequest
from app.services.llm_service import LLMService


@dataclass
class FixedAgent:
    name: str
    result: ActionResult
    allowed_tools: frozenset = frozenset()
    description: str = "离线夹具"

    async def run(self, state, instruction):
        return self.result


def decision(action, **kwargs):
    return {"action": action, "reason": "测试下一步", **kwargs}


def parsed(intent="create", **request):
    return ActionResult(data={"intent": intent, "trip_request": request})


def itinerary(cost=0):
    return Itinerary.model_validate({
        "summary": "离线预置行程", "days": [{"day_index": 1, "items": [
            {"item_id": "item-1", "place_id": "poi-1", "name": "夹具景点",
             "start_time": "09:00", "duration_minutes": 60, "estimated_cost": cost}
        ], "total_cost": cost}], "total_cost": cost,
    })


def registry(*agents):
    result = AgentRegistry()
    for agent in agents:
        result.register(agent)
    return result


async def test_fake_flow_plans_validates_saves_and_finishes():
    draft = itinerary()
    agents = registry(
        FixedAgent("requirement", parsed(destination="长沙", days=1)),
        FixedAgent("attraction_search", ActionResult(data={"candidates": []})),
        FixedAgent("planner", ActionResult(data={"draft_itinerary": draft.model_dump(mode="json")})),
    )
    trip_service = AsyncMock()
    trip_service.save_version.return_value = (7, 1)
    supervisor = FakeSupervisorAgent([
        decision("call_agent", target_agent="requirement"),
        decision("call_agent", target_agent="attraction_search"),
        decision("call_agent", target_agent="planner"),
        decision("validate"), decision("save"), decision("finish"),
    ])
    initial = {"user_message": "去长沙玩一天"}
    state = await build_autonomous_graph().ainvoke(
        initial, context=AutonomousGraphContext(
            supervisor=supervisor, agent_registry=agents, trip_service=trip_service))
    assert state["status"] == "completed"
    assert state["saved_version_no"] == 1
    assert state["agent_call_count"] == 3
    assert [s["action"] for s in state["steps"]] == [
        "call_agent", "call_agent", "call_agent", "validate", "save", "finish"]
    assert [s["index"] for s in state["steps"]] == list(range(1, 7))
    assert state["pending_update"] is None
    trip_service.save_version.assert_awaited_once()
    assert trip_service.save_version.await_args.args[-1].passed
    assert initial == {"user_message": "去长沙玩一天"}
    assert "llm" not in state and "trip_service" not in state and "controller" not in state


@pytest.mark.parametrize("intent,target", [("create", "attraction_search"), ("revise", "planner"),
                                         ("knowledge", "place_knowledge")])
async def test_rule_supervisor_selects_agent_for_intent(intent, target):
    state = {"requirements_parsed": True, "intent": intent,
             "trip_request": TripRequest(destination="长沙", days=1),
             "current_itinerary": itinerary()}
    result = await SupervisorAgent().decide(state)
    assert result.action == "call_agent"
    assert result.target_agent == target


@pytest.mark.parametrize("text,status", [
    ("去长沙", "needs_input"), ("请帮我写代码", "failed"),
    ("修改第二天", "needs_input"), ("岳麓书院有什么历史", "failed"),
])
async def test_default_graph_without_credentials_has_honest_exits(text, status):
    state = await build_autonomous_graph().ainvoke({"user_message": text})
    assert state["status"] == status
    assert state["steps"]
    assert state["tool_call_count"] == 0
    if "历史" in text:
        assert "尚未实现" in state["response"]
        assert state["knowledge_result"].status == "unimplemented"


async def test_create_with_candidates_reaches_planner_stub_without_claiming_success():
    agents = build_default_agent_registry()
    agents._agents["requirement"] = FixedAgent("requirement", parsed(destination="长沙", days=1))
    agents._agents["attraction_search"] = FixedAgent("attraction_search", ActionResult(data={"candidates": []}))
    state = await build_autonomous_graph(agent_registry=agents).ainvoke({"user_message": "去长沙玩一天"})
    assert state["status"] == "failed"
    assert state["planner_result"].status == "unimplemented"
    assert state["draft_itinerary"] is None
    assert state["saved_version_no"] is None


@pytest.mark.parametrize("output", [
    {"action": "execute_python"}, {"action": "call_agent"},
    {"action": "save", "finish": True},
    {"action": "finish", "target_tool": "x"},
    {"action": "fail", "python": "print(1)"},
    {"action": "fail", "finish": "true"}, "not JSON",
])
async def test_invalid_structure_enters_traced_fail(output):
    state = await build_autonomous_graph(supervisor=FakeSupervisorAgent([output])).ainvoke({})
    assert state["status"] == "failed"
    assert state["steps"][-1]["action"] == "fail"
    assert state["steps"][-1]["error"]
    assert state["agent_call_count"] == state["tool_call_count"] == 0


@pytest.mark.parametrize("constructed", [False, True])
async def test_supervisor_tool_action_is_rejected_before_execution(constructed):
    tools = ToolRegistry()
    handler = AsyncMock(return_value=ActionResult())
    tools.register(ToolSpec(query_tool(handler), frozenset({"worker"})))
    agents = registry(FixedAgent("worker", ActionResult(), frozenset({"query"})))
    # 即使自定义主管绕过模型构造，Graph 也必须拒绝已删除的动作。
    output = (SupervisorDecision.model_construct(action="call_tool", target_agent="worker")
              if constructed else decision("call_tool", target_agent="worker", target_tool="query",
                                           metadata={"args": {}}))
    supervisor = AsyncMock()
    supervisor.decide.return_value = output
    graph = build_autonomous_graph(supervisor=supervisor, agent_registry=agents, tool_registry=tools)
    assert "call_tool" not in graph.get_graph().nodes
    state = await graph.ainvoke({})
    assert state["status"] == "failed"
    assert "结构校验失败" in state["error"]
    assert [step["action"] for step in state["steps"]] == ["fail"]
    assert state["agent_call_count"] == state["tool_call_count"] == 0
    handler.assert_not_awaited()


async def test_unknown_agent_denial_is_traced():
    value = decision("call_agent", target_agent="unknown")
    state = await build_autonomous_graph(supervisor=FakeSupervisorAgent([value])).ainvoke({})
    assert state["status"] == "failed"
    assert "未知 Agent" in state["error"]
    assert state["steps"][-1]["decision"]["metadata"]["rejected_decision"] == SupervisorDecision.model_validate(value).model_dump(mode="json")


async def test_both_sides_of_tool_permission_are_required():
    agents = registry(FixedAgent("allowed_by_tool", ActionResult()))
    tools = ToolRegistry()
    tools.register(ToolSpec(query_tool(), frozenset({"allowed_by_tool"})))
    with pytest.raises(RegistryError, match="无权"):
        tools.assert_allowed("allowed_by_tool", "query", agents)
    agents = registry(FixedAgent("declares_tool", ActionResult(), frozenset({"query"})))
    with pytest.raises(RegistryError, match="无权"):
        tools.assert_allowed("declares_tool", "query", agents)


@pytest.mark.parametrize("spec", [
    ToolSpec(query_tool(name="save_version"), frozenset({"planner"})),
    ToolSpec(query_tool(name="write"), frozenset({"planner"}), "write", False),
])
def test_write_tools_cannot_be_registered(spec):
    with pytest.raises(RegistryError):
        ToolRegistry().register(spec)


@pytest.mark.parametrize("kind", ["supervisor", "agent", "tool"])
async def test_execution_errors_count_attempt_and_do_not_expose_secret(kind):
    async def broken(*args):
        raise RuntimeError("https://upstream/?key=secret")

    agents = registry(FixedAgent("worker", ActionResult(), frozenset({"query"})))
    tools = ToolRegistry()
    tools.register(ToolSpec(query_tool(broken), frozenset({"worker"})))
    if kind == "supervisor":
        supervisor = FakeSupervisorAgent(broken)
    elif kind == "agent":
        agents.get("worker").run = broken
        supervisor = FakeSupervisorAgent([decision("call_agent", target_agent="worker")])
    else:
        async def run_with_tool(state, instruction):
            return await tools.call("query", {})

        agents.get("worker").run = run_with_tool
        supervisor = FakeSupervisorAgent([decision("call_agent", target_agent="worker")])
    state = await build_autonomous_graph(
        supervisor=supervisor, agent_registry=agents, tool_registry=tools).ainvoke({})
    assert state["status"] == "failed"
    assert "secret" not in str(state)
    assert state["steps"][-1]["error"]
    assert state["agent_call_count"] == (0 if kind == "supervisor" else 1)
    assert state["tool_call_count"] == (1 if kind == "tool" else 0)
    if kind != "supervisor":
        assert state["last_result"].status == "failed"


@pytest.mark.parametrize("name,field", [("attraction_search", "attraction_result"),
                                       ("place_knowledge", "knowledge_result")])
async def test_failed_agent_contract_preserves_data_and_tool_trace(name, field):
    tools = ToolRegistry()
    tools.register(ToolSpec(query_tool(lambda state, args: ActionResult(data={"count": 1})),
                            frozenset({name})))
    agent_name = name

    class Worker:
        name = agent_name
        description = ""
        allowed_tools = frozenset({"query"})

        async def run(self, state, instruction):
            await tools.call("query", {})
            return ActionResult(status="failed", message="业务筛选失败", data={"attempts": 1})

    state = await build_autonomous_graph(supervisor=FakeSupervisorAgent([
        decision("call_agent", target_agent=name), decision("finish")
    ]), agent_registry=registry(Worker()), tool_registry=tools).ainvoke({})
    assert state["status"] == "failed"
    assert len(state["steps"]) == 1
    assert state[field] == state["last_result"]
    assert state[field].data == {"attempts": 1}
    assert state[field].message == "业务筛选失败"
    assert state["steps"][0]["result"] == state[field].model_dump(mode="json")
    assert state["steps"][0]["tool_calls"][0]["status"] == "completed"
    assert state["tool_call_count"] == 1


@pytest.mark.parametrize("limit,error", [
    (RuntimeLimits(10, 20, 20), "最大循环次数"),
    (RuntimeLimits(12, 2, 20), "最大 Agent"),
])
async def test_limits_stop_repeated_agent_calls(limit, error):
    supervisor = FakeSupervisorAgent(lambda s: decision("call_agent", target_agent="worker"))
    agents = registry(FixedAgent("worker", ActionResult()))
    state = await build_autonomous_graph(limits=limit).ainvoke(
        {}, context=AutonomousGraphContext(supervisor=supervisor, agent_registry=agents))
    assert state["status"] == "failed"
    assert error in state["error"]
    assert state["iteration"] <= limit.max_iterations
    assert state["agent_call_count"] <= limit.max_agent_calls


async def test_agents_share_tool_budget_with_separate_scopes():
    tools = ToolRegistry()
    handler = AsyncMock(return_value=ActionResult(data={"value": 1}))
    tools.register(ToolSpec(query_tool(handler), frozenset({"first", "second"})))

    class Worker:
        description = "嵌套调用"
        allowed_tools = frozenset({"query"})

        def __init__(self, name, calls):
            self.name = name
            self.calls = calls

        async def run(self, state, instruction):
            for _ in range(self.calls):
                await tools.call("query", {})
            return ActionResult()

    agents = registry(Worker("first", 1), Worker("second", 2))
    supervisor = FakeSupervisorAgent([
        decision("call_agent", target_agent="first"),
        decision("call_agent", target_agent="second"),
    ])
    state = await build_autonomous_graph(
        supervisor=supervisor, agent_registry=agents, tool_registry=tools,
        limits=RuntimeLimits(10, 5, 2)).ainvoke({})
    assert state["status"] == "failed"
    assert "最大工具" in state["error"]
    assert state["agent_call_count"] == 2
    assert state["tool_call_count"] == handler.await_count == 2
    assert len(state["steps"][-1]["tool_calls"]) == 2
    assert [step["tool_calls"][0]["agent"] for step in state["steps"]] == ["first", "second"]
    with pytest.raises(RegistryError, match="作用域"):
        await tools.call("query", {})


async def test_unauthorized_nested_call_cannot_be_hidden_by_agent():
    tools = build_default_tool_registry()

    class Worker:
        name = "worker"
        description = ""
        allowed_tools = frozenset()

        async def run(self, state, instruction):
            try:
                await tools.call("estimate_itinerary_cost", {})
            except RegistryError:
                pass
            return ActionResult()

    state = await build_autonomous_graph(
        supervisor=FakeSupervisorAgent([decision("call_agent", target_agent="worker")]),
        agent_registry=registry(Worker()), tool_registry=tools).ainvoke({})
    assert state["status"] == "failed"
    assert state["steps"][0]["tool_calls"][0]["status"] == "rejected"


@pytest.mark.parametrize("validation", [None, {"passed": False}, {"passed": True}])
async def test_save_rejects_unvalidated_or_user_forged_validation(validation):
    trip_service = AsyncMock()
    state = await build_autonomous_graph(
        supervisor=FakeSupervisorAgent([decision("save")]),
        context=AutonomousGraphContext(trip_service=trip_service)).ainvoke({
        "draft_itinerary": itinerary(), "validation_result": validation,
    })
    assert state["status"] == "failed"
    assert "拒绝保存" in state["error"]
    trip_service.save_version.assert_not_awaited()


@pytest.mark.parametrize("changed", ["draft_itinerary", "trip_request", "route_info"])
async def test_save_rejects_stale_validation(changed):
    trip_service = AsyncMock()
    graph = build_autonomous_graph(
        supervisor=FakeSupervisorAgent([decision("validate"), decision("save")]),
        context=AutonomousGraphContext(trip_service=trip_service), checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "stale-validation"}}
    state = await graph.ainvoke(
        {"draft_itinerary": itinerary(), "trip_request": TripRequest(destination="长沙", days=1)},
        config, interrupt_after=["merge_result"])
    assert state["validation_result"].passed
    if changed == "draft_itinerary":
        state[changed] = itinerary(3)
    elif changed == "trip_request":
        state[changed].budget = 1
    else:
        from app.schemas.trip_schema import RouteInfo
        state[changed] = [RouteInfo(from_item_id="a", to_item_id="b", distance_km=1,
                                   duration_minutes=10, mode="walking")]
    # 在已校验的 checkpoint 中修改业务数据，验证保存节点会拒绝旧证明。
    await graph.aupdate_state(config, {changed: state[changed]}, as_node="merge_result")
    state = await graph.ainvoke(None, config)
    assert state["status"] == "failed"
    trip_service.save_version.assert_not_awaited()


async def test_trip_service_stub_and_duplicate_save_do_not_report_success():
    for saved, expected_calls in [(None, 1), ((9, 1), 1)]:
        trip_service = AsyncMock()
        trip_service.save_version.return_value = saved
        graph = build_autonomous_graph(supervisor=FakeSupervisorAgent([
            decision("validate"), decision("save"), decision("save")
        ]), context=AutonomousGraphContext(trip_service=trip_service))
        state = await graph.ainvoke({"draft_itinerary": itinerary()})
        assert state["status"] == "failed"
        assert trip_service.save_version.await_count == expected_calls


async def test_agent_mutation_is_isolated_and_updates_wait_for_merge():
    class Worker:
        name = "requirement"
        description = ""
        allowed_tools = frozenset()

        async def run(self, state, instruction):
            state["agent_call_count"] = -100
            state["trip_request"].destination = "北京"
            return parsed("create", days=2, preferences=[], budget=0)

    graph = build_autonomous_graph(supervisor=FakeSupervisorAgent([
        decision("call_agent", target_agent="requirement")]), agent_registry=registry(Worker()),
        checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "deferred-merge"}}
    state = await graph.ainvoke({"trip_request": TripRequest(
        destination="长沙", preferences=["历史"], budget=100)}, config,
        interrupt_after=["call_agent"])
    assert state["trip_request"].days is None
    assert state["agent_call_count"] == 0
    assert state["steps"] == []
    state = await graph.ainvoke(None, config, interrupt_after=["merge_result"])
    assert state["agent_call_count"] == 1
    assert len(state["steps"]) == 1
    assert state["pending_update"] is None
    assert state["trip_request"].destination == "长沙"
    assert state["trip_request"].days == 2
    assert state["trip_request"].preferences == []
    assert state["trip_request"].budget == 0


@pytest.mark.parametrize("checkpoint", [False, True])
async def test_followup_resets_turn_and_preserves_request(checkpoint):
    graph = build_autonomous_graph(checkpointer=InMemorySaver() if checkpoint else None)
    config = {"configurable": {"thread_id": "followup"}}
    first = await graph.ainvoke({"user_message": "去长沙"}, config)
    assert first["status"] == "needs_input"
    second = await graph.ainvoke({**({} if checkpoint else first), "user_message": "玩三天"}, config)
    assert first["trip_request"].days is None
    assert second["trip_request"].destination == "长沙"
    assert second["trip_request"].days == 3
    assert second["status"] == "failed"
    assert "LLMService" in second["response"]
    assert second["run_id"] != first["run_id"]
    assert second["pending_question"] is None
    assert second["steps"][0]["iteration"] == 1
    assert all(s["run_id"] == second["run_id"] for s in second["steps"])
    assert [s["index"] for s in second["steps"]] == list(range(1, len(second["steps"]) + 1))
    assert all(s["run_id"] == first["run_id"] for s in first["steps"])


async def test_same_graph_separates_runtime_contexts():
    graph = build_autonomous_graph()
    async def run(city):
        agents = registry(FixedAgent("requirement", parsed(destination=city)))
        return await graph.ainvoke({"user_message": "旅行"}, context=AutonomousGraphContext(
            agent_registry=agents, supervisor=RuleBasedSupervisorAgent()))
    a, b = await asyncio.gather(run("长沙"), run("北京"))
    assert a["trip_request"].destination == "长沙"
    assert b["trip_request"].destination == "北京"
    assert a["steps"] is not b["steps"]


async def test_llm_supervisor_uses_real_structured_parser_and_schema():
    llm = LLMService(FakeListChatModel(responses=[
        json.dumps(decision("call_agent", target_agent="attraction_search")),
        "not JSON",
    ]))
    tools = build_default_tool_registry()
    agents = build_default_agent_registry(tool_registry=tools)
    supervisor = SupervisorAgent(llm, agents)
    first = await supervisor.decide({"intent": "create"})
    assert first.target_agent == "attraction_search"
    state = await build_autonomous_graph(
        supervisor=supervisor, agent_registry=agents, tool_registry=tools).ainvoke({})
    assert state["status"] == "failed"
    assert state["steps"][0]["action"] == "fail"


async def test_load_failure_does_not_call_supervisor():
    trip_service = AsyncMock()
    trip_service.load_current.side_effect = RuntimeError("secret")
    supervisor = AsyncMock()
    ctx = AutonomousGraphContext(trip_service=trip_service, supervisor=supervisor)
    state = await build_autonomous_graph().ainvoke({"trip_id": 3}, context=ctx)
    assert state["status"] == "failed"
    assert state["steps"][0]["action"] == "load_context"
    assert "secret" not in str(state)
    supervisor.decide.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    "不是 JSON", '{"intent":"unknown"}',
    '{"intent":"create","trip_request":{"days":-2}}',
])
async def test_bad_requirement_output_never_calls_search(payload):
    llm = LLMService(FakeListChatModel(responses=[payload]))
    from unittest.mock import AsyncMock
    amap = AsyncMock()
    state = await build_autonomous_graph().ainvoke({"user_message": "去长沙"}, context=AutonomousGraphContext(
        llm=llm, amap=amap, supervisor=RuleBasedSupervisorAgent()))
    assert state["status"] == "failed"
    assert state["agent_call_count"] == 1
    assert state["tool_call_count"] == 0
    amap.search_places_by_query.assert_not_awaited()


@pytest.mark.parametrize("intent,required", [
    ("create", ["destination", "days"]), ("direct_search", ["destination"]),
])
async def test_missing_fields_are_checked_by_code_after_patch_merge(intent, required):
    llm = LLMService(FakeListChatModel(responses=[json.dumps({
        "intent": intent, "trip_request": {}, "missing_fields": []})]))
    state = await build_autonomous_graph(agent_registry=registry(RequirementAgent(llm))).ainvoke(
        {"user_message": "旅行"})
    assert state["status"] == "needs_input"
    assert state["missing_fields"] == required


@pytest.mark.parametrize("intent,target", [("revise", "planner"), ("knowledge", "place_knowledge")])
async def test_parsed_intents_reach_the_expected_stub(intent, target):
    agents = build_default_agent_registry()
    agents._agents["requirement"] = FixedAgent("requirement", parsed(intent))
    state = await build_autonomous_graph(agent_registry=agents).ainvoke({
        "user_message": "需求", "current_itinerary": itinerary()})
    assert state["status"] == "failed"
    assert state["steps"][1]["decision"]["target_agent"] == target
    assert state["last_result"].status == "unimplemented"


async def test_graph_keeps_builder_supervisor_when_context_supplies_services_only():
    supervisor = FakeSupervisorAgent([decision("ask_user", instruction="请补充需求")])
    state = await build_autonomous_graph(supervisor=supervisor).ainvoke(
        {"user_message": "旅行"}, context=AutonomousGraphContext(validator=AsyncMock()))
    assert state["status"] == "needs_input"
    assert state["agent_call_count"] == 0


async def test_failed_deterministic_validation_blocks_trip_service():
    trip_service = AsyncMock()
    state = await build_autonomous_graph(supervisor=FakeSupervisorAgent([
        decision("validate"), decision("save")]),
        context=AutonomousGraphContext(trip_service=trip_service)).ainvoke({
            "draft_itinerary": itinerary(10),
            "trip_request": TripRequest(destination="长沙", days=1, budget=1)})
    assert state["validation_result"].passed is False
    assert state["status"] == "failed"
    trip_service.save_version.assert_not_awaited()


async def test_update_builders_preserve_input_and_defer_trace_until_merge():
    class MutatingSupervisor:
        async def decide(self, state):
            assert state["iteration"] == 1
            state["trip_request"].destination = "北京"
            state["steps"].clear()
            return decision("ask_user")

    controller = AgentRuntime(MutatingSupervisor())
    state = await controller.initialize({"trip_request": TripRequest(destination="长沙")})
    state["steps"] = [{"index": 1, "action": "previous"}]
    before = deepcopy(state)
    update = await controller.decide_update(state)
    assert state == before
    assert set(update) == {"iteration", "last_decision"}
    state.update(update)
    before = deepcopy(state)
    update = await controller.execute_update(state)
    assert state == before
    assert set(update) == {"pending_update"}
    assert len(update["pending_update"]["steps"]) == 1
    state.update(update)
    before = deepcopy(state)
    merged = controller.merge_update(state)
    assert state == before
    assert merged["pending_update"] is None
    assert [s["index"] for s in merged["steps"]] == [2]
    assert controller.merge_update({**state, "pending_update": None}) == {"pending_update": None}


@pytest.mark.parametrize("changed", ["trip_request", "draft_itinerary", "route_info"])
async def test_business_update_clears_validation_and_saved_proof(changed):
    draft = itinerary()
    tools = ToolRegistry()
    tools.register(ToolSpec(query_tool(
        lambda state, args: ActionResult(data={"route_info": []}), name="calculate_route"),
        frozenset({"planner"})))

    class RoutePlanner(FixedAgent):
        async def run(self, state, instruction):
            result = await tools.call("calculate_route", {})
            return ActionResult(data={"draft_itinerary": draft.model_dump(mode="json"), **result.data})

    planner_type = RoutePlanner if changed == "route_info" else FixedAgent
    agents = registry(
        FixedAgent("requirement", parsed(destination="长沙", days=1)),
        planner_type("planner", ActionResult(data={"draft_itinerary": draft.model_dump(mode="json")}),
                     frozenset({"calculate_route"})),
    )
    change = decision("call_agent", target_agent="requirement" if changed == "trip_request" else "planner")
    supervisor = FakeSupervisorAgent([
        decision("validate"), decision("save"), change, decision("save"),
    ])
    trip_service = AsyncMock()
    trip_service.save_version.return_value = (7, 1)
    initial = {"draft_itinerary": draft, "trip_request": TripRequest(destination="长沙", days=1)}
    state = await build_autonomous_graph(
        supervisor=supervisor, agent_registry=agents, tool_registry=tools,
        context=AutonomousGraphContext(trip_service=trip_service)).ainvoke(initial)
    assert state["status"] == "failed"
    assert "拒绝保存" in state["error"]
    assert all(state[key] is None for key in (
        "validation_result", "validation_fingerprint", "saved_version_no", "saved_fingerprint"))
    assert [s["index"] for s in state["steps"]] == [1, 2, 3, 4]
    assert state["tool_call_count"] == (1 if changed == "route_info" else 0)
    trip_service.save_version.assert_awaited_once()


@pytest.mark.parametrize("checkpoint", [False, True])
async def test_followup_load_failure_replaces_old_traces(checkpoint):
    graph = build_autonomous_graph(checkpointer=InMemorySaver() if checkpoint else None)
    config = {"configurable": {"thread_id": "load-failure"}}
    first = await graph.ainvoke({"user_message": "去长沙"}, config)
    trip_service = AsyncMock()
    trip_service.load_current.side_effect = RuntimeError("secret")
    supervisor = AsyncMock()
    state = await graph.ainvoke(
        {**({} if checkpoint else first), "trip_id": 3}, config,
        context=AutonomousGraphContext(trip_service=trip_service, supervisor=supervisor))
    assert state["status"] == "failed"
    assert len(state["steps"]) == 1
    assert state["steps"][0]["action"] == "load_context"
    assert state["steps"][0]["run_id"] == state["run_id"] != first["run_id"]
    assert state["steps"][0]["index"] == 1
    supervisor.decide.assert_not_awaited()
