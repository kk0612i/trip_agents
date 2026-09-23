"""Planner 图构建与单次规划编排；协议、提交及证据验收各自独立。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from time import perf_counter
from typing import TYPE_CHECKING

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.graph.state import CompiledStateGraph
from pydantic import ValidationError

from app.agent.planner.errors import PlanningError
from app.agent.planner.checks import collect_places, normalize_draft, verify_cost, verify_routes
from app.agent.planner.middleware import PlannerMiddleware
from app.agent.planner.output import SUBMIT_PLAN
from app.core.log import logger, safe_log_identifier
from app.prompts.planner_prompt import PLANNER_AGENT_SYSTEM_PROMPT
from app.schemas.agent_schema import ActionResult
from app.schemas.planner_schema import _PlannerResult
from app.schemas.trip_schema import Itinerary, PlaceCandidate, TripRequest
from app.tools.context import ToolContext
from app.tools.errors import RegistryError, ToolExecutionError
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from loguru import Logger

    from app.agent.supervisor.supervisor_state import AgentRunState


def build_planner_graph(
    llm: BaseChatModel,
    tools: ToolRegistry,
    places: Mapping[str, PlaceCandidate],
    log: Logger,
    *,
    budget: int,
) -> CompiledStateGraph:
    """构建本轮规划图，内部结果提交独立于公共业务工具账本。

    Args:
        llm: 调用方注入并拥有的模型。
        tools: 已建立 Planner 作用域的工具注册表。
        places: 当前运行可引用的可信地点证据。
        log: 绑定当前运行编号的日志对象。
        budget: 本轮剩余的业务查询次数，模型额外保留一轮最终提交。

    Returns:
        不持久化检查点的规划图，每次调用创建独立协议状态。
    """
    return create_agent(
        model=llm,
        tools=[*tools.tools_for("planner", names=("calculate_route", "estimate_itinerary_cost")),
               SUBMIT_PLAN],
        context_schema=ToolContext,
        system_prompt=PLANNER_AGENT_SYSTEM_PROMPT,
        middleware=[PlannerMiddleware(tools, places, log),
                    ModelCallLimitMiddleware(run_limit=budget + 1, exit_behavior="error")],
        checkpointer=False,
    )


class PlannerAgent:
    """在注入的工具预算内，由模型自主查询并编排可验证的行程草稿。"""

    name = "planner"
    description = "自主查询路线和费用、组合或修改行程，不编造地点与路线事实"
    allowed_tools = frozenset({"calculate_route", "estimate_itinerary_cost"})

    def __init__(
        self, llm: BaseChatModel | None, tools: ToolRegistry, *, max_tool_calls: int = 3,
    ) -> None:
        """注入模型和工具注册表，不在构造期间建立外部连接。

        Args:
            llm: 调用方管理生命周期的模型；为空时返回未实现结果。
            tools: 提供统一权限、调用额度及工具轨迹的注册表。
            max_tool_calls: 单次规划允许的业务工具调用上限，不含最终提交。

        Raises:
            ValueError: 工具调用上限小于 1。
        """
        if max_tool_calls < 1:
            raise ValueError("规划工具调用上限必须大于 0")
        self.llm, self.tools, self.max_tool_calls = llm, tools, max_tool_calls

    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """执行有界规划循环，工具轨迹仍由 Runtime 保存。

        Args:
            state: 本轮主管状态，读取但不原地修改。
            instruction: 主管给规划 Agent 的具体任务。

        Returns:
            完成的行程草稿及证据，或安全的失败、未实现结果。
        """
        log = logger.bind(run_id=safe_log_identifier(state.get("run_id")))
        started = perf_counter()
        log.debug("event=planner_started status=running")
        result = await self._run(state, instruction, log)
        log.bind(event="planner_completed", status=result.status,
                 duration_ms=round((perf_counter() - started) * 1000, 2)).info(
            "event=planner_completed status={} duration_ms={:.2f}",
            result.status, (perf_counter() - started) * 1000)
        return result

    async def _run(
        self, state: AgentRunState, instruction: str, log: Logger,
    ) -> ActionResult:
        """驱动图调用并将可公开的输入、协议和证据错误转换为动作结果。"""
        if self.llm is None:
            return ActionResult(status="unimplemented", message="行程规划需要注入 LLMService")
        try:
            intent = state.get("intent")
            if intent not in {"create", "revise"}:
                raise PlanningError("行程规划需要明确的 create 或 revise 意图")
            request = TripRequest.model_validate(state.get("trip_request") or {})
            current = None
            if intent == "revise":
                if state.get("current_itinerary") is None:
                    raise PlanningError("修改行程需要提供已有行程")
                current = Itinerary.model_validate(state["current_itinerary"])
            if intent == "create" and (not (request.destination or "").strip() or not request.days):
                raise PlanningError("新建行程缺少目的地或天数")
            days = request.days or len(current.days)
            places, search = collect_places(state, current)
            if not places:
                raise PlanningError("没有可用于规划的真实候选地点")
            context = self.tools.current_context()
            trace_start = len(context.traces)
            budget = min(self.max_tool_calls, context.remaining - context.calls)
            if budget < 1:
                raise PlanningError("规划工具调用额度不足")
            context.remaining = context.calls + budget
            agent = build_planner_graph(self.llm, self.tools, places, log, budget=budget)
            output = await agent.ainvoke({"messages": [HumanMessage(content=json.dumps({
                "intent": intent, "request": request.model_dump(mode="json"),
                "user_message": state.get("user_message", ""), "instruction": instruction,
                "current_itinerary": current.model_dump(mode="json") if current else None,
                "candidates": [place.model_dump(mode="json") for place in places.values()],
                "recommendations": search.get("recommendations", []),
                "unmet_conditions": search.get("unmet_conditions", []),
                "days": days, "max_tool_calls": budget,
            }, ensure_ascii=False))]}, config={"recursion_limit": 4 * budget + 10}, context=context)
            # 最终提交是内部输出工具，不是业务查询；既保持结构化校验，又兼容 auto 工具选择。
            final = output["messages"][-1]
            if not isinstance(final, ToolMessage) or final.name != "_PlannerResult" or final.status == "error":
                raise PlanningError("模型未提交有效的结构化规划结果")
            plan = _PlannerResult.model_validate(final.artifact)
            if context.violation:
                raise RegistryError(context.violation)
            if plan.status == "failed":
                raise PlanningError("模型根据当前工具结果无法完成规划")
            if plan.draft_itinerary is None:
                raise PlanningError("模型未提交行程草稿")
            draft = plan.draft_itinerary
            normalize_draft(draft, places, request, days, current)
            records = {record["tool_call_id"]: record for record in context.traces[trace_start:]
                       if record.get("status") == "completed"}
            routes = verify_routes(draft, plan.route_selections, records)
            cost = verify_cost(draft, plan.cost_call_id, records)
            draft.total_cost = cost.data["known_total"]
            for day in draft.days:
                day.warnings.extend(search.get("unmet_conditions", []))
                if cost.data["unknown_items"]:
                    day.warnings.append("费用仅为已知项目小计，完整旅行费用及预算仍待核实。")
                day.warnings = list(dict.fromkeys(day.warnings))
            log.debug("event=planner_evidence_checked days={} route_count={} tool_calls={}",
                      days, len(routes), len(context.traces) - trace_start)
            return ActionResult(message="已生成行程草稿，仍需校验后保存", data={
                "draft_itinerary": draft.model_dump(mode="json"),
                "route_info": [route.model_dump(mode="json") for route in routes],
                "cost_estimate": cost.data,
            })
        except (PlanningError, RegistryError, ToolExecutionError) as exc:
            log.bind(event="planner_rejected", error_type=type(exc).__name__).warning(
                "event=planner_rejected error_type={}", type(exc).__name__)
            return ActionResult(status="failed", message=str(exc))
        except ValidationError:
            log.bind(event="planner_rejected", error_type="ValidationError").warning(
                "event=planner_rejected error_type=ValidationError")
            return ActionResult(status="failed", message="规划输入或模型输出结构不正确")
        except Exception as exc:
            log.bind(event="planner_failed", error_type=type(exc).__name__).error(
                "event=planner_failed error_type={}", type(exc).__name__)
            return ActionResult(status="failed", message="行程规划执行失败，未生成可用草稿")
