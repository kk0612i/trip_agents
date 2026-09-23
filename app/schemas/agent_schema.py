"""主管决策与专业任务结果的公共契约。"""

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ActionResult(BaseModel):
    """专业任务和应用工具统一返回契约。"""

    # 专业任务执行状态；未实现与外部能力暂不可用分别表示。
    status: Literal["completed", "failed", "unimplemented", "unavailable"] = "completed"
    message: str = ""  # 面向主管及用户的安全结果说明，不携带上游原始错误。
    data: dict[str, Any] = Field(default_factory=dict)  # 不同专业任务的结构化业务载荷。


# 主管单步决策；类说明使用注释，避免改变模型已冻结的 JSON Schema 描述。
class SupervisorDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["ask_user", "call_agent", "validate", "save", "finish", "fail"]  # 本步动作名。
    target_agent: str | None = None  # 仅 call_agent 可指定已注册的专业 Agent。
    instruction: str = ""  # 传给专业任务的执行要求，或系统动作的用户说明。
    reason: str = ""  # 可观察的调度理由，不要求输出模型内部思考。
    finish: bool | None = Field(default=None, strict=True)  # 省略时由动作是否终态推导。
    metadata: dict[str, Any] = Field(default_factory=dict)  # 附加元数据，不作为权限凭证。

    @model_validator(mode="after")
    def validate_action(self) -> Self:
        """校验动作与目标、终态标记的一致性，并补齐终态标记。

        Returns:
            完成一致性检查的当前决策。

        Raises:
            ValueError: 专业目标或显式终态标记与动作不一致。
        """
        if self.action == "call_agent":
            if not self.target_agent or not self.target_agent.strip():
                raise ValueError("call_agent 需要 target_agent")
        elif self.target_agent is not None:
            raise ValueError("系统动作不能指定 Agent")
        terminal = self.action in {"ask_user", "finish", "fail"}
        if self.finish is not None and self.finish != terminal:
            raise ValueError("finish 与 action 不一致")
        self.finish = terminal
        return self
