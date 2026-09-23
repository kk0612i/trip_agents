"""搜索住宿地点，不执行预订的专业能力骨架。"""

from dataclasses import dataclass

from app.agent.stub import StubAgent
from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import ActionResult


class AccommodationSearchAgent:
    """搜索住宿地点，不执行预订；待实现业务能力，沿用占位 run。"""

    name: str = "accommodation"
    description: str = "搜索住宿地点，不执行预订"
    allowed_tools: frozenset[str] = frozenset({"search_accommodation", "get_place_detail", "calculate_route"})
    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """明确返回未实现状态，不修改行程或触发外部调用。

        Args:
            state: 当前运行状态的独立快照，供未来实现读取。
            instruction: 主管的专业任务说明，供未来实现使用。

        Returns:
            状态为 unimplemented 的既有业务结果。
        """
        return ActionResult(status="unimplemented", message=f"{self.name} Agent 尚未实现")