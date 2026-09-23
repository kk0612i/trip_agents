"""专业 Agent 的注册、发现和默认装配；工具由 app.tools 管理。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.tools.errors import RegistryError
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from app.agent.base import SpecialistAgent
    from app.services.llm_service import LLMService


class AgentRegistry:
    """专业 Agent 注册入口，拒绝重复名称和未注册调用。"""

    def __init__(self) -> None:
        """创建本次装配的 Agent 表，注册项不保存单轮状态。"""
        self._agents: dict[str, SpecialistAgent] = {}

    def register(self, agent: SpecialistAgent) -> None:
        """登记专业 Agent，不接管其注入资源的生命周期。

        Args:
            agent: 实现统一 run 契约并声明名称、说明和权限的任务实例。

        Raises:
            RegistryError: Agent 名称为空或已经注册。
        """
        if not agent.name or agent.name in self._agents:
            raise RegistryError("Agent 名称为空或重复")
        self._agents[agent.name] = agent

    def get(self, name: str) -> SpecialistAgent:
        """按稳定注册名读取专业 Agent。

        Args:
            name: 已注册的专业任务名称。

        Returns:
            注册时持有的任务实例，不为每次调用重新创建。

        Raises:
            RegistryError: 名称未注册。
        """
        if name not in self._agents:
            raise RegistryError(f"未知 Agent：{name}")
        return self._agents[name]

    def exists(self, name: str | None) -> bool:
        """判断目标是否已注册。

        Args:
            name: 待检查名称；None 表示未指定专业任务。

        Returns:
            名称已注册时为 True，未提供或未注册时为 False。
        """
        return name in self._agents

    def allowed_tools(self, name: str) -> frozenset[str]:
        """取得 Agent 声明的工具权限，不替代工具侧白名单检查。

        Args:
            name: 已注册的专业任务名称。

        Returns:
            不可变工具名集合；执行层还需检查工具侧授权。

        Raises:
            RegistryError: 专业任务名称未注册。
        """
        return frozenset(self.get(name).allowed_tools)

    def descriptions(self) -> list[dict[str, Any]]:
        """生成主管决策使用的能力目录。

        Returns:
            按注册顺序排列的名称、任务说明和工具权限。
        """
        return [{"name": a.name, "description": a.description,
                 "allowed_tools": sorted(a.allowed_tools)} for a in self._agents.values()]


def build_default_agent_registry(
    *, llm_service: LLMService | None = None,
    tool_registry: ToolRegistry | None = None,
) -> AgentRegistry:
    """装配既有专业任务及具名能力占位，不创建外部资源。

    Args:
        llm_service: 外部持有的模型服务；None 时保留各任务的既有离线行为。
        tool_registry: 统一工具入口；None 时使用空注册表，由调用方显式装配能力。

    Returns:
        保持既有注册名称和权限集合的 Agent 注册表。
    """
    from app.agent.place_search.place_search_graph import PlaceSearchAgent
    from app.agent.requirement import RequirementAgent
    from app.agent.accommodation import AccommodationSearchAgent
    from app.agent.place_knowledge import PlaceKnowledgeAgent
    from app.agent.weather_impact import WeatherImpactAgent
    from app.agent.planner.planner_graph import PlannerAgent

    llm = llm_service.llm if llm_service is not None else None
    tools = tool_registry or ToolRegistry()
    registry = AgentRegistry()
    registry.register(RequirementAgent(llm_service))
    registry.register(PlaceSearchAgent(llm=llm, tools=tools))
    registry.register(PlannerAgent(llm=llm, tools=tools))

    registry.register(AccommodationSearchAgent())
    registry.register(PlaceKnowledgeAgent())
    registry.register(WeatherImpactAgent())
    return registry
