"""测试专用的脚本化 Agent 替身，不由生产代码导入。"""

import inspect
from collections.abc import Callable, Sequence
from typing import Any

from app.schemas.agent_schema import SupervisorDecision


class FakeSupervisorAgent:
    """脚本化离线决策；序列索引取自运行状态，不在实例上累积跨会话游标。"""

    def __init__(self, decisions: Sequence[Any] | Callable[[dict], Any]) -> None:
        self.decisions = decisions

    async def decide(self, state: dict) -> SupervisorDecision:
        if callable(self.decisions):
            value = self.decisions(state)
            value = await value if inspect.isawaitable(value) else value
        else:
            index = state["iteration"] - 1
            if index >= len(self.decisions):
                raise ValueError("FakeSupervisor 决策已耗尽")
            value = self.decisions[index]
        return SupervisorDecision.model_validate(value)
