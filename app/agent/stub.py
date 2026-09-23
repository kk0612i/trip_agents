"""未实现专业能力的统一占位结果，不生成虚假的业务成果。"""

from dataclasses import dataclass

from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import ActionResult


@dataclass(frozen=True)
class StubAgent:
    """保留名称、任务边界与工具权限，供未来专业实现替换。"""

    name: str  # 稳定的注册名称。
    description: str  # 面向主管的能力边界说明。
    allowed_tools: frozenset[str]  # 声明权限；占位执行本身不调用工具。

    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """明确返回未实现状态，不修改行程或触发外部调用。

        Args:
            state: 当前运行状态的独立快照，供未来实现读取。
            instruction: 主管的专业任务说明，供未来实现使用。

        Returns:
            状态为 unimplemented 的既有业务结果。
        """
        return ActionResult(status="unimplemented", message=f"{self.name} Agent 尚未实现")
