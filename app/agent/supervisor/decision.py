"""Supervisor 只决定下一步动作，不执行查询或业务写入。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent.supervisor.fallback import RuleBasedSupervisorAgent
from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import SupervisorDecision

if TYPE_CHECKING:
    from app.agent.registry import AgentRegistry
    from app.services.llm_service import LLMService


class SupervisorAgent:
    """复用注入的模型服务；未注入时使用规则回退，不读取密钥配置。"""

    allowed_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        llm_service: LLMService | None = None,
        agent_registry: AgentRegistry | None = None,
    ) -> None:
        """保存决策依赖，依赖生命周期由外部调用方管理。

        Args:
            llm_service: 已注入的模型服务；None 表示使用规则回退。
            agent_registry: 提供名称、说明与权限的专业 Agent 注册表。
        """
        self.llm_service = llm_service
        self.agents = agent_registry

    async def decide(self, state: AgentRunState) -> SupervisorDecision:
        """选择下一步动作，工具权限和保存凭证交由执行层复核。

        Args:
            state: 当前业务状态与执行轨迹的独立快照。

        Returns:
            经过结构校验的主管决策。

        Raises:
            ValueError: 已注入模型却缺少专业 Agent 注册表。
        """
        if self.llm_service is None:
            return await RuleBasedSupervisorAgent().decide(state)
        if self.agents is None:
            raise ValueError("LLM Supervisor 需要 Agent 注册表")
        # 轨迹留给审计，模型只接收当前业务结果，避免反复嵌入全部工具输出。
        view = {key: value for key, value in state.items()
                if key not in {"steps", "pending_update"}}
        result = await self.llm_service.supervisor_decide(
            view, self.agents.descriptions())
        return SupervisorDecision.model_validate(result)
