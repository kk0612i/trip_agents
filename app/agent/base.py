"""专业任务与主管决策的共享接口。"""

from typing import Protocol

from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import ActionResult, SupervisorDecision


class SpecialistAgent(Protocol):
    """只处理自身任务，不写数据库；state 是当前运行状态的独立快照。"""

    name: str  # 注册表中的稳定 Agent 名称。
    description: str  # 提供给主管的任务边界说明。
    allowed_tools: frozenset[str]  # 本 Agent 获准调用的应用工具名。

    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """执行当前专业任务，不直接覆盖主管运行状态。

        Args:
            state: 当前运行状态的独立快照。
            instruction: 主管下达的任务说明。

        Returns:
            结构化结果；可预期失败返回 failed，未实现返回 unimplemented，保留轨迹。
        """
        ...


class DecisionProvider(Protocol):
    """可替换的异步决策接口，输出仍由 AgentRuntime 复核。"""

    async def decide(self, state: AgentRunState) -> SupervisorDecision:
        """选择下一步动作，权限及调用预算仍由执行层复核。

        Args:
            state: 当前运行状态及已经取得的业务结果。

        Returns:
            经过结构校验的主管决策。
        """
        ...
