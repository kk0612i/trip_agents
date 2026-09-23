"""LangGraph 只编排状态转换，业务调度由 SupervisorAgent 决定。"""

from __future__ import annotations

from dataclasses import fields, replace
from typing import Any

from langchain_core.runnables import Runnable
from langgraph.checkpoint.base import BaseCheckpointSaver

from app.agent.base import DecisionProvider
from app.agent.registry import AgentRegistry
from app.services.trip_service import TripService
from app.tools.registry import ToolRegistry

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Overwrite

from app.agent.supervisor.context import AutonomousGraphContext
from app.agent.registry import build_default_agent_registry
from app.tools.registry import build_default_tool_registry
from app.agent.supervisor.runtime import AgentRuntime, RuntimeLimits, TERMINAL
from app.agent.supervisor.supervisor_state import AgentRunState
from app.agent.supervisor.decision import SupervisorAgent


def build_autonomous_graph(
    *,
    checkpointer: BaseCheckpointSaver | None = None,
    context: AutonomousGraphContext | None = None,
    supervisor: DecisionProvider | None = None,
    agent_registry: AgentRegistry | None = None,
    tool_registry: ToolRegistry | None = None,
    trip_service: TripService | None = None,
    limits: RuntimeLimits | None = None,
) -> Runnable[AgentRunState, dict[str, Any]]:
    """构建异步自主循环，支持每次 ainvoke 注入独立运行依赖。

    Args:
        checkpointer: 外部管理的检查点存储；None 表示不持久化检查点。
        context: 构图默认依赖；每次调用的非空 Context 字段可覆盖它。
        supervisor: 显式主管决策器，覆盖默认 Context 中同名字段。
        agent_registry: 显式专业 Agent 注册表。
        tool_registry: 显式受控公共工具注册表。
        trip_service: 显式旅行服务，通过短会话执行加载，保存仍为占位。
        limits: 每轮执行预算；None 时使用默认上限。

    Returns:
        带递归上限的异步图；调用方仍拥有传入服务及检查点资源。
    """
    limits = limits or RuntimeLimits()
    defaults = replace(context) if context else AutonomousGraphContext()
    for name, value in (("supervisor", supervisor), ("agent_registry", agent_registry),
                        ("tool_registry", tool_registry), ("trip_service", trip_service)):
        if value is not None:
            setattr(defaults, name, value)

    def controller(runtime: Runtime[AutonomousGraphContext]) -> AgentRuntime:
        """合并本次调用的依赖覆盖，为当前节点组装独立执行控制器。"""
        supplied = runtime.context
        ctx = replace(defaults, **{
            field.name: getattr(supplied, field.name)
            for field in fields(AutonomousGraphContext)
            if supplied is not None and getattr(supplied, field.name) is not None
        })
        tools = ctx.tool_registry or build_default_tool_registry(amap_service=ctx.amap)
        agents = ctx.agent_registry or build_default_agent_registry(llm_service=ctx.llm, tool_registry=tools)
        decider = ctx.supervisor or SupervisorAgent(ctx.llm, agents)
        return AgentRuntime(decider, agents, tools, validator=ctx.validator,
                            trip_service=ctx.trip_service, limits=limits)

    async def load_context(state: AgentRunState, runtime: Runtime[AutonomousGraphContext]) -> dict[str, Any]:
        """加载正式版本并初始化本轮状态，用覆盖写入清除旧轮次轨迹。"""
        initialized = await controller(runtime).initialize(state)
        # 新轮次覆盖旧轨迹；加载失败时仍保留本轮初始化产生的错误记录。
        return {**initialized, "steps": Overwrite(initialized["steps"])}

    async def supervisor_node(state: AgentRunState, runtime: Runtime[AutonomousGraphContext]) -> dict[str, Any]:
        """调用本次运行的主管决策器，返回尚未执行的决策更新。"""
        return await controller(runtime).decide_update(state)

    async def execute_node(state: AgentRunState, runtime: Runtime[AutonomousGraphContext]) -> dict[str, Any]:
        """交给执行层复核权限和预算，执行最近决策并暂存状态补丁。"""
        return await controller(runtime).execute_update(state)

    def merge_result(state: AgentRunState, runtime: Runtime[AutonomousGraphContext]) -> dict[str, Any]:
        """合并已校验的动作结果，保持更新责任集中在执行层。"""
        return controller(runtime).merge_update(state)


    graph = StateGraph(AgentRunState, context_schema=AutonomousGraphContext)
    graph.add_node("load_context", load_context)
    graph.add_node("supervisor", supervisor_node)
    graph.add_node("merge_result", merge_result)
    actions = ("ask_user", "call_agent", "validate", "save", "finish", "fail")
    for action in actions:
        graph.add_node(action, execute_node)
        graph.add_edge(action, "merge_result")
    graph.add_edge(START, "load_context")
    graph.add_conditional_edges("load_context",
                                lambda s: "end" if s["status"] in TERMINAL else "supervisor",
                                {"end": END, "supervisor": "supervisor"})
    graph.add_conditional_edges("supervisor", lambda s: s["last_decision"].action,
                                {action: action for action in actions})
    graph.add_conditional_edges("merge_result",
                                lambda s: "end" if s["status"] in TERMINAL else "supervisor",
                                {"end": END, "supervisor": "supervisor"})
    # 每次业务迭代占三个 Graph superstep，预留加载和失败出口的调度步数。
    return graph.compile(checkpointer=checkpointer).with_config(
        recursion_limit=3 * limits.max_iterations + 8)
