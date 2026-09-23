"""LangGraph 节点使用的有界决策与动作执行规则。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import inspect
import json
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from pydantic_core import to_jsonable_python

from app.agent.registry import AgentRegistry, build_default_agent_registry
from app.tools.errors import RegistryError
from app.tools.registry import ToolRegistry, build_default_tool_registry
from app.agent.base import DecisionProvider
from app.agent.supervisor.fallback import RuleBasedSupervisorAgent
from app.agent.supervisor.decision import SupervisorAgent
from app.agent.supervisor.supervisor_state import AgentRunState
from app.services.trip_service import TripService
from app.schemas.agent_schema import ActionResult, SupervisorDecision
from app.schemas.trip_schema import Itinerary, RouteInfo, TripRequest, ValidationResult
from app.schemas.requirement_schema import MinimalParsedRequest
from app.services.validation_service import ValidationService
from app.core.log import logger, log_event, node_log, safe_log_identifier
from app.core.errors import CapabilityUnavailableError

TERMINAL = {"completed", "needs_input", "failed"}
RESULT_FIELDS = {
    "attraction_search": "attraction_result", "accommodation": "accommodation_result",
    "planner": "planner_result", "place_knowledge": "knowledge_result",
    "weather_impact": "weather_result",
}


class RuntimeRuleError(RuntimeError):
    """可直接展示的框架规则错误，不包含外部响应内容。"""


@dataclass(frozen=True, slots=True)
class RuntimeLimits:
    """次数以一次 Supervisor 决策、一次 Agent 执行、一次工具执行计。"""

    max_iterations: int = 12  # 每轮主管决策次数上限，必须为正数。
    max_agent_calls: int = 8  # 每轮专业任务调用次数上限，可为零。
    max_tool_calls: int = 8  # 每轮公共工具调用次数上限，不计内部结果提交。

    def __post_init__(self) -> None:
        if self.max_iterations < 1 or self.max_agent_calls < 0 or self.max_tool_calls < 0:
            raise ValueError("循环上限必须为正数，调用上限不能为负数")


class AgentRuntime:
    """控制权限、预算和状态合并；专业 Agent 不能直接修改运行器状态。"""

    def __init__(self, supervisor: DecisionProvider | None = None,
                 agent_registry: AgentRegistry | None = None,
                 tool_registry: ToolRegistry | None = None, *,
                 validator: ValidationService | None = None, trip_service: TripService | None = None,
                 limits: RuntimeLimits | None = None) -> None:
        """保存执行依赖，校验权限与预算由本运行器负责。

        Args:
            supervisor: 下一步决策器；None 时使用规则回退。
            agent_registry: 专业任务注册表；None 时组装默认 Agent。
            tool_registry: 受控公共工具注册表；None 时组装默认工具。
            validator: 确定性校验器；None 时创建无资源校验服务。
            trip_service: 本次运行独占的旅行服务；调用方管理会话关闭，服务管理事务。
                缺省时拒绝加载和保存，不得跨并发运行共享。
            limits: 单轮调用上限；None 时使用默认预算。
        """
        self.supervisor = supervisor or RuleBasedSupervisorAgent()
        self.tools = tool_registry or build_default_tool_registry()
        self.agents = agent_registry or build_default_agent_registry(tool_registry=self.tools)
        self.validator = validator or ValidationService()
        self.trip_service = trip_service
        self.limits = limits or RuntimeLimits()

    async def initialize(self, source: AgentRunState | None = None) -> AgentRunState:
        """开始新一轮；保留需求，清除上轮结果、校验证明、计数和错误。

        Args:
            source: 当前用户输入或上轮检查点；None 表示空白输入。

        Returns:
            与输入隔离的新运行状态；加载异常转为失败状态和本轮错误轨迹。
        """
        source = source or {}
        state = {
            "run_id": uuid4().hex, "trip_id": source.get("trip_id"),
            "user_message": source.get("user_message", ""),
            "trip_request": source.get("trip_request"), "intent": source.get("intent"),
            "current_itinerary": source.get("current_itinerary"),
            "current_version_no": None,
            # 显式提供的草稿可用于框架测试，旧运行返回的草稿不跨新用户消息复用。
            "draft_itinerary": source.get("draft_itinerary") if "run_id" not in source else None,
            "route_info": source.get("route_info", []) if "run_id" not in source else [],
            "validation_result": None, "validation_fingerprint": None, "saved_fingerprint": None,
            "last_decision": None, "last_result": None, "pending_update": None,
            "steps": [], "iteration": 0, "agent_call_count": 0, "tool_call_count": 0,
            "retry_count": 0, "response": None, "error": None, "pending_question": None,
            "status": "running", "requirements_parsed": False,
            "missing_fields": [], "search_keywords": [], "saved_version_no": None,
            **{field: None for field in RESULT_FIELDS.values()},
        }
        state = deepcopy(state)
        started_at = perf_counter()
        log_event("agent_run_started", run_id=state["run_id"], node="load_context", status="running")
        try:
            for key, model in (("trip_request", TripRequest), ("current_itinerary", Itinerary),
                               ("draft_itinerary", Itinerary)):
                if state[key] is not None:
                    state[key] = model.model_validate(state[key])
            state["route_info"] = TypeAdapter(list[RouteInfo]).validate_python(state["route_info"])
            if state["trip_id"] is not None:
                if self.trip_service is None:
                    raise RuntimeRuleError("指定旅行编号时必须注入 TripService")
                # 服务在返回前关闭本次短会话，后续模型推理不持有数据库连接。
                with logger.contextualize(run_id=state["run_id"]):
                    current = await self.trip_service.load_current(state["trip_id"])
                if current is None:
                    raise RuntimeRuleError("旅行不存在或尚无已保存行程")
                state["current_version_no"], itinerary = current
                state["current_itinerary"] = Itinerary.model_validate(itinerary)
        except Exception as exc:
            message = str(exc) if isinstance(exc, RuntimeRuleError) else f"加载上下文失败：{type(exc).__name__}"
            state.update(status="failed", error=message, response=message)
            self._trace(state, "load_context", None, message)
            log_event("agent_run_finished", level="WARNING" if isinstance(exc, RuntimeRuleError) else "ERROR",
                      run_id=state["run_id"], node="load_context", status="failed", iteration=0,
                      error_type=type(exc).__name__, duration_ms=round((perf_counter() - started_at) * 1000, 2))
        return state

    @node_log
    async def decide_update(self, state: AgentRunState) -> dict[str, Any]:
        """验证模型结构和执行前置条件；拒绝时转为可追踪的 fail 动作。

        Args:
            state: 本轮运行状态，决策器只取得独立快照。

        Returns:
            本次决策与迭代次数补丁；终态不再产生新动作。
        """
        if state["status"] in TERMINAL:
            return {}
        iteration = state["iteration"]
        decision = None
        try:
            if iteration >= self.limits.max_iterations:
                raise RuntimeRuleError("达到最大循环次数")
            iteration += 1
            raw = await self.supervisor.decide(deepcopy({**state, "iteration": iteration}))
            # model_construct 等绕过模型构造的结果也必须重新经过验证。
            if isinstance(raw, SupervisorDecision):
                raw = raw.model_dump()
            decision = SupervisorDecision.model_validate(raw)
            self._check_decision(decision, state)
        except Exception as exc:
            log_event("agent_decision_rejected", level="WARNING", node="supervisor", status="rejected",
                      iteration=iteration, error_type=type(exc).__name__)
            message = self._error_message(exc, "Supervisor 决策")
            rejected = decision.model_dump(mode="json") if decision else None
            decision = SupervisorDecision(action="fail", reason=message,
                                          metadata={"rejected_decision": rejected})
        if isinstance(self.supervisor, RuleBasedSupervisorAgent) or (
            isinstance(self.supervisor, SupervisorAgent) and self.supervisor.llm_service is None
        ):
            log_event("supervisor_rule_fallback", level="WARNING", node="supervisor", iteration=iteration)
        log_event("agent_action_selected", node="supervisor", action=decision.action,
                  target=safe_log_identifier(decision.target_agent), iteration=iteration)
        return {"iteration": iteration, "last_decision": decision}

    @node_log
    async def execute_update(self, state: AgentRunState) -> dict[str, Any]:
        """执行一个动作，将变更暂存到 pending_update，等待 Graph 合并节点。

        Args:
            state: 含已校验主管决策的当前状态。

        Returns:
            待合并补丁；成功和失败均保留公共工具调用轨迹。
        """
        decision = state["last_decision"]
        started_at = perf_counter()
        updates: dict[str, Any] = {}
        result = None
        error = None
        error_type = None
        failure_level = "WARNING"
        tool_traces = []
        try:
            self._check_decision(decision, state)
            action = decision.action
            if action == "call_agent":
                with self.tools.scope(decision.target_agent, self.agents, state,
                                      self.limits.max_tool_calls - state["tool_call_count"]) as ledger:
                    try:
                        updates["agent_call_count"] = state["agent_call_count"] + 1
                        agent = self.agents.get(decision.target_agent)
                        raw = agent.run(deepcopy(state), decision.instruction)
                        raw = await raw if inspect.isawaitable(raw) else raw
                        result = ActionResult.model_validate(raw)
                        if ledger.violation:
                            raise RuntimeRuleError(ledger.violation)
                    finally:
                        updates["tool_call_count"] = state["tool_call_count"] + ledger.calls
                        tool_traces = ledger.traces
                updates.update(self._agent_updates(decision.target_agent, result, state))
                if result.status == "failed":
                    raise RuntimeRuleError(result.message or "任务返回失败")
            elif action == "validate":
                validation = self._validate(state)
                result = ActionResult(data=validation.model_dump(mode="json"))
                updates.update(validation_result=validation,
                               validation_fingerprint=self._fingerprint(state))
            elif action == "save":
                if self.trip_service is None:
                    raise RuntimeRuleError("未注入 TripService，不能保存")
                # 保存前再运行确定性规则，校验对象本身不能作为写入授权凭据。
                validation = self._validate(state)
                if not self._passed(validation):
                    raise RuntimeRuleError("校验未通过，拒绝保存")
                saved = await self.trip_service.save_version(
                    state["trip_id"], deepcopy(state["trip_request"]), None,
                    deepcopy(state["draft_itinerary"]), deepcopy(state["route_info"]), validation)
                if (not isinstance(saved, tuple) or len(saved) != 2
                        or any(type(v) is not int or v <= 0 for v in saved)):
                    raise RuntimeRuleError("TripService 未返回有效版本，保存未完成")
                trip_id, version = saved
                updates.update(trip_id=trip_id, saved_version_no=version,
                               saved_fingerprint=self._fingerprint(state),
                               current_itinerary=deepcopy(state["draft_itinerary"]),
                               current_version_no=version)
                result = ActionResult(data={"trip_id": trip_id, "version_no": version})
            elif action == "ask_user":
                question = decision.instruction or decision.reason or "请补充旅行需求"
                updates.update(status="needs_input", response=question, pending_question=question)
            elif action == "finish":
                updates.update(status="completed", response=decision.instruction or decision.reason or "处理完成")
            elif action == "fail":
                error = decision.reason or decision.instruction or "Supervisor 判定失败"
                updates.update(status="failed", error=error, response=error)
        except Exception as exc:
            error_type = type(exc).__name__
            if not isinstance(exc, (RuntimeRuleError, RegistryError, ValidationError, CapabilityUnavailableError)):
                failure_level = "ERROR"
            error = self._error_message(exc, decision.action)
            if decision.action == "call_agent":
                # 所有 Agent 共用失败契约；框架拒绝也保留已返回的数据和工具账本。
                result = ActionResult(status="failed", message=error,
                                      data=result.data if result is not None else {})
                if decision.target_agent in RESULT_FIELDS:
                    updates[RESULT_FIELDS[decision.target_agent]] = result
            updates.update(status="failed", error=error, response=error)
        if result is not None:
            updates["last_result"] = result
        updates["steps"] = [self._step_trace(
            state, decision.action, result, error, tool_traces)]
        log_event("agent_action_completed", level=failure_level if error else "INFO", error_type=error_type,
                  node=decision.action, action=decision.action, target=safe_log_identifier(decision.target_agent),
                  iteration=state["iteration"], status="failed" if error else (result.status if result else updates.get("status", "completed")),
                  agent_call_count=updates.get("agent_call_count", state["agent_call_count"]),
                  tool_call_count=updates.get("tool_call_count", state["tool_call_count"]),
                  duration_ms=round((perf_counter() - started_at) * 1000, 2))
        return {"pending_update": updates}

    def merge_update(self, state: AgentRunState) -> dict[str, Any]:
        """统一合并业务变更，失效的校验证明和已保存标记不能沿用。

        Args:
            state: 包含本次动作 pending_update 的运行状态。

        Returns:
            可供 Graph 合并的业务补丁，同时清除暂存区。
        """
        updates = dict(state.get("pending_update") or {})
        # 按字段是否出现判定失效，即使更新值相同也不能沿用旧证明。
        if any(key in updates for key in ("trip_request", "draft_itinerary", "route_info")):
            updates.update(validation_result=None, validation_fingerprint=None,
                           saved_version_no=None, saved_fingerprint=None)
        if updates.get("status") in TERMINAL:
            log_event("agent_run_finished", level="WARNING" if updates["status"] == "failed" else "INFO",
                      run_id=safe_log_identifier(state.get("run_id")), node="merge_result",
                      status=updates["status"], iteration=state["iteration"],
                      agent_call_count=updates.get("agent_call_count", state["agent_call_count"]),
                      tool_call_count=updates.get("tool_call_count", state["tool_call_count"]))
        return {**updates, "pending_update": None}

    def _check_decision(self, decision: SupervisorDecision, state: AgentRunState) -> None:
        if decision.action == "call_agent":
            self.agents.get(decision.target_agent)
            if state["agent_call_count"] >= self.limits.max_agent_calls:
                raise RuntimeRuleError("达到最大 Agent 调用次数")
        elif decision.action == "save":
            validation = state.get("validation_result")
            if (validation is None or not self._passed(validation)
                    or state.get("validation_fingerprint") != self._fingerprint(state)):
                raise RuntimeRuleError("当前草稿未通过校验或校验已失效，拒绝保存")
            if state.get("saved_fingerprint") == self._fingerprint(state):
                raise RuntimeRuleError("当前草稿已保存，拒绝重复保存")
        elif decision.action == "finish":
            last = state.get("last_result")
            if not last or ActionResult.model_validate(last).status != "completed":
                raise RuntimeRuleError("没有已完成的业务结果，不能 finish")
            intent = state.get("intent")
            if intent in {"create", "revise"}:
                if (not state.get("saved_version_no")
                        or state.get("saved_fingerprint") != self._fingerprint(state)):
                    raise RuntimeRuleError("行程尚未校验保存，不能 finish")
            elif intent in {"direct_search", "knowledge"}:
                key = "attraction_result" if intent == "direct_search" else "knowledge_result"
                evidence = state.get(key)
                if not evidence or ActionResult.model_validate(evidence).status != "completed":
                    raise RuntimeRuleError("目标任务尚未完成，不能 finish")
            else:
                raise RuntimeRuleError("未支持的意图不能 finish")

    def _agent_updates(self, name: str, result: ActionResult, state: AgentRunState) -> dict[str, Any]:
        updates = {RESULT_FIELDS[name]: result} if name in RESULT_FIELDS else {}
        if result.status != "completed":
            return updates
        if name == "requirement":
            parsed = MinimalParsedRequest.model_validate(result.data)
            values = state["trip_request"].model_dump() if state["trip_request"] else {}
            if parsed.trip_request is not None:
                values.update(parsed.trip_request.model_dump(exclude_none=True))
            request = TripRequest.model_validate(values)
            required = ["destination", "days"] if parsed.intent == "create" else (
                ["destination"] if parsed.intent == "direct_search" else [])
            missing = [key for key in required if not getattr(request, key)]
            updates.update(intent=parsed.intent, trip_request=request,
                           search_keywords=parsed.search_keywords, missing_fields=missing,
                           requirements_parsed=True)
        elif name == "planner":
            updates["draft_itinerary"] = Itinerary.model_validate(result.data["draft_itinerary"])
            updates["route_info"] = TypeAdapter(list[RouteInfo]).validate_python(
                result.data.get("route_info", []))
        return updates

    def _validate(self, state: AgentRunState) -> ValidationResult:
        if state.get("draft_itinerary") is None:
            raise RuntimeRuleError("没有可校验的草稿")
        request = state.get("trip_request")
        return self.validator.validate(deepcopy(state["draft_itinerary"]),
                                       deepcopy(state["route_info"]), request.budget if request else None)

    @staticmethod
    def _passed(validation: ValidationResult) -> bool:
        result = ValidationResult.model_validate(validation)
        return result.passed and not any(issue.severity == "error" for issue in result.issues)

    @staticmethod
    def _fingerprint(state: AgentRunState) -> str:
        inputs = {key: state.get(key) for key in ("draft_itinerary", "trip_request", "route_info")}
        encoded = json.dumps(to_jsonable_python(inputs), sort_keys=True, ensure_ascii=False)
        return sha256(encoded.encode()).hexdigest()

    @staticmethod
    def _error_message(exc: Exception, operation: str) -> str:
        if isinstance(exc, (RuntimeRuleError, RegistryError)):
            return str(exc)
        if isinstance(exc, ValidationError):
            return f"{operation} 结构校验失败"
        return f"{operation} 执行异常：{type(exc).__name__}"

    @staticmethod
    def _step_trace(
        state: AgentRunState,
        action: str,
        result: ActionResult | None,
        error: str | None,
        tool_traces: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        decision = state.get("last_decision")
        return {"index": len(state["steps"]) + 1, "run_id": state["run_id"],
                "iteration": state["iteration"], "action": action,
                "decision": decision.model_dump(mode="json") if decision else None,
                "result": result.model_dump(mode="json") if result else None,
                "error": error, "tool_calls": tool_traces or [],
                "agent_call_count_before": state["agent_call_count"],
                "tool_call_count_before": state["tool_call_count"]}

    def _trace(
        self, state: AgentRunState, action: str,
        result: ActionResult | None, error: str | None,
    ) -> None:
        state["steps"].append(self._step_trace(state, action, result, error))
