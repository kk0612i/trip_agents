"""回答带来源的景点知识，不修改计划的专业能力骨架。"""

from dataclasses import dataclass

from app.agent.stub import StubAgent


@dataclass(frozen=True)
class PlaceKnowledgeAgent(StubAgent):
    """回答带来源的景点知识，不修改计划；待实现业务能力，沿用占位 run。"""

    name: str = "place_knowledge"
    description: str = "回答带来源的景点知识，不修改计划"
    allowed_tools: frozenset[str] = frozenset()
