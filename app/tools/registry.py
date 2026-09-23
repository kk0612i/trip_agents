"""工具注册、双向授权和单次动作作用域管理。"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from collections.abc import Iterator

if TYPE_CHECKING:
    from app.agent.supervisor.supervisor_state import AgentRunState
    from app.services.amap_service import AmapService
from uuid import uuid4

from langchain.tools import ToolRuntime
from langchain_core.tools import BaseTool
from pydantic import ValidationError

from app.schemas.agent_schema import ActionResult
from app.tools.context import AgentPermissions, ToolContext
from app.tools.errors import RegistryError


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """注册时冻结的工具授权及副作用声明。

    Attributes:
        tool: 实际执行的同一个 LangChain 工具实例。
        allowed_agents: 工具侧白名单，还需与 Agent 侧权限取交集。
        side_effect_level: 副作用等级，当前只允许 none 与 read。
        read_only: 是否只读；写入须走 persistence 系统动作。
        implemented: 未实现工具不可通过工具发现伪装成可用能力。
    """

    tool: BaseTool
    allowed_agents: frozenset[str]
    side_effect_level: Literal["none", "read", "write"] = "read"
    read_only: bool = True
    implemented: bool = True


class ToolRegistry:
    """持有工具定义，并通过 ContextVar 隔离并发 Agent 的执行账本。"""

    def __init__(self, *, amap_service: AmapService | None = None) -> None:
        """注入高德服务；其资源仍由应用或调用方拥有。"""
        self._tools: dict[str, ToolSpec] = {}
        self._amap_service = amap_service
        self._scope: ContextVar[ToolContext | None] = ContextVar("trip_tool_context", default=None)

    def register(self, spec: ToolSpec) -> None:
        """注册只读工具，拒绝重名、写入能力或未使用统一执行入口的工具。"""
        name = spec.tool.name
        if not name or name in self._tools:
            raise RegistryError("工具名称为空或重复")
        if (not spec.read_only or spec.side_effect_level not in {"none", "read"}
                or name in {"save", "save_version", "write_database"}):
            raise RegistryError("写入和版本保存只能通过 persistence 系统动作执行")
        if getattr(getattr(spec.tool, "coroutine", None), "tool_guard_name", None) != name:
            raise RegistryError("注册工具必须使用 guarded_tool 统一执行入口")
        self._tools[name] = spec

    def get_spec(self, name: str) -> ToolSpec:
        """按名称取得注册契约；未注册工具明确抛出 RegistryError。"""
        if name not in self._tools:
            raise RegistryError(f"未知工具：{name}")
        return self._tools[name]

    def get(self, name: str) -> BaseTool:
        """返回注册时持有的工具对象，不在 Agent 中重新包装。"""
        return self.get_spec(name).tool

    def exists(self, name: str | None) -> bool:
        """判断工具名是否已经注册，包括声明为未实现的工具。"""
        return name in self._tools

    def assert_allowed(self, agent_name: str, name: str, agents: AgentPermissions) -> ToolSpec:
        """检查 Agent 与工具两侧权限。

        Args:
            agent_name: 当前执行作用域的专业任务名。
            name: 本次请求的公共工具名。
            agents: 提供 Agent 侧授权集合的接口。

        Returns:
            同时通过两侧授权的工具注册契约。

        Raises:
            RegistryError: 工具未注册或任一侧未授权。
        """
        agent_tools = agents.allowed_tools(agent_name)
        spec = self.get_spec(name)
        if agent_name not in spec.allowed_agents or name not in agent_tools:
            raise RegistryError(f"Agent {agent_name} 无权调用工具 {name}")
        return spec

    def current_context(self) -> ToolContext:
        """取得当前异步执行链的可信工具上下文。

        Returns:
            当前仍有效且属于本注册表的执行上下文。

        Raises:
            RegistryError: 尚未进入作用域或上下文已经失效。
        """
        context = self._scope.get()
        self.require_context(context)
        assert context is not None  # require_context 已拒绝空上下文。
        return context

    def require_context(self, context: ToolContext | None) -> None:
        """拒绝缺失、跨作用域或已经失效的上下文。

        Args:
            context: 待核验上下文；None 表示调用方没有可信作用域。

        Raises:
            RegistryError: 上下文不属于当前有效作用域。
        """
        if context is None or context is not self._scope.get() or not context.active:
            raise RegistryError("工具必须由 AgentRuntime 建立调用作用域")

    def tools_for(self, agent_name: str, *, names: tuple[str, ...] | None = None) -> list[BaseTool]:
        """按双方权限筛选工具；显式越权请求会保留拒绝轨迹。

        Args:
            agent_name: 必须与当前作用域身份一致的专业任务名称。
            names: 指定要发现的工具；None 时自动筛选已实现且双方允许的工具。

        Returns:
            注册时持有的已实现工具，不重新包装工具描述或协议。

        Raises:
            RegistryError: 上下文无效、身份不匹配或显式请求了未授权工具。
        """
        context = self.current_context()
        if agent_name != context.agent_name:
            raise RegistryError("不能获取其他 Agent 的工具")
        if names is None:
            names = tuple(name for name, spec in self._tools.items()
                          if spec.implemented and agent_name in spec.allowed_agents
                          and name in context.agents.allowed_tools(agent_name))
        result = []
        for name in names:
            try:
                spec = self.assert_allowed(agent_name, name, context.agents)
            except RegistryError as exc:
                context.violation = str(exc)
                context.traces.append({"tool": name, "agent": agent_name,
                                       "status": "rejected", "error": str(exc)})
                raise
            if spec.implemented:
                result.append(spec.tool)
        return result

    @contextmanager
    def scope(
        self, agent_name: str, agents: AgentPermissions, state: AgentRunState, remaining: int,
    ) -> Iterator[ToolContext]:
        """为单次专业任务创建独立上下文，并在退出时使其失效。

        Args:
            agent_name: 当前 Agent 注册名称。
            agents: Agent 侧权限查询接口。
            state: 主管状态；进入作用域时深拷贝，不由工具修改原对象。
            remaining: 本轮可接受的调用总数，失败调用也消费额度。

        Yields:
            当前异步链独占的工具上下文，退出后不得复用。
        """
        context = ToolContext(self, agent_name, agents, deepcopy(state), remaining, self._amap_service)
        token = self._scope.set(context)
        try:
            yield context
        finally:
            context.active = False
            self._scope.reset(token)

    async def call(self, name: str, arguments: dict[str, Any]) -> ActionResult:
        """在可信作用域内经统一工具入口执行调用。

        Args:
            name: 已注册的公共工具名。
            arguments: 模型或专业任务提供的业务参数，不得包含 runtime。

        Returns:
            工具产出的结构化结果；参数和授权拒绝不会伪装成成功。

        Raises:
            RegistryError: 上下文无效、参数含运行上下文或不满足参数契约。
        """
        context = self.current_context()
        try:
            tool = self.get(name)
            if "runtime" in arguments:
                raise RegistryError("工具运行上下文不能由参数指定")
            arguments = tool.args_schema.model_validate({**arguments, "runtime": None}).model_dump()
        except (RegistryError, ValidationError) as exc:
            context.violation = str(exc) if isinstance(exc, RegistryError) else "工具参数格式不正确"
            context.traces.append({"tool": name, "agent": context.agent_name,
                                   "status": "rejected", "error": context.violation})
            raise RegistryError(context.violation) from exc
        call_id = uuid4().hex
        runtime = ToolRuntime(state={}, context=context, config={}, stream_writer=lambda _: None,
                              tool_call_id=call_id, store=None)
        message = await tool.ainvoke({"name": name, "type": "tool_call", "id": call_id,
                                     "args": {**arguments, "runtime": runtime}})
        return ActionResult.model_validate(message.artifact)

    def descriptions(self) -> list[dict[str, Any]]:
        """导出工具协议和权限元数据，不创建模型或外部连接。"""
        return [{"name": spec.tool.name, "description": spec.tool.description,
                 "allowed_agents": sorted(spec.allowed_agents),
                 "side_effect_level": spec.side_effect_level, "read_only": spec.read_only,
                 "arguments": spec.tool.tool_call_schema.model_json_schema(),
                 "implemented": spec.implemented} for spec in self._tools.values()]


def build_default_tool_registry(*, amap_service: AmapService | None = None) -> ToolRegistry:
    """注册六个公共工具，保持工具名和双向权限集合不变。

    Args:
        amap_service: 调用方拥有的高德服务；None 时相关工具明确报告能力未实现。

    Returns:
        共用统一执行入口和权限约束的工具注册表。
    """
    from app.tools.amap import (
        calculate_route, get_place_detail, get_weather_forecast,
        search_accommodation, search_attractions,
    )
    from app.tools.cost import estimate_itinerary_cost

    registry = ToolRegistry(amap_service=amap_service)
    for spec in (
        ToolSpec(search_attractions, frozenset({"attraction_search"})),
        ToolSpec(get_place_detail, frozenset({"attraction_search", "accommodation"})),
        ToolSpec(calculate_route, frozenset({"planner", "accommodation"})),
        ToolSpec(estimate_itinerary_cost, frozenset({"planner"}), "none"),
        ToolSpec(search_accommodation, frozenset({"accommodation"})),
        ToolSpec(get_weather_forecast, frozenset({"weather_impact"})),
    ):
        registry.register(spec)
    return registry
