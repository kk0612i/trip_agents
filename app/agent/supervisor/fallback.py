"""不依赖模型的生产规则回退，保持既有意图路由和退出规则。"""

from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import ActionResult, SupervisorDecision


class RuleBasedSupervisorAgent:
    """按当前已取得的结果选择下一动作，供离线和无模型场景使用。"""

    async def decide(self, state: AgentRunState) -> SupervisorDecision:
        """根据已有结果选择下一动作，实际执行前置条件仍由 Runtime 校验。

        Args:
            state: 当前运行状态，允许仅提供已取得的业务字段。

        Returns:
            与模型主管相同协议的下一步决策。
        """
        def action(name: str, instruction: str = "", **kwargs: str) -> SupervisorDecision:
            """将规则选择转换为统一决策，由 Schema 复核动作约束。"""
            return SupervisorDecision(action=name, instruction=instruction, reason=instruction, **kwargs)

        if state.get("error"):
            return action("fail", state["error"])
        if not state.get("requirements_parsed"):
            return action("call_agent", "解析用户需求和意图", target_agent="requirement")
        last = state.get("last_result")
        if last is not None:
            last = ActionResult.model_validate(last)
            if last.status != "completed":
                return action("fail", last.message or "当前能力尚未实现")
        intent = state.get("intent")
        if intent in {"other", "unsupported", None}:
            return action("fail", "该意图尚未支持")
        if state.get("missing_fields"):
            return action("ask_user", "请补充：" + "、".join(state["missing_fields"]))
        if intent == "revise" and state.get("current_itinerary") is None:
            return action("ask_user", "请提供需要修改的已有行程或旅行编号")
        if intent == "knowledge":
            if state.get("knowledge_result") is None:
                return action("call_agent", "回答景点知识并标注来源", target_agent="place_knowledge")
            return action("finish", ActionResult.model_validate(state["knowledge_result"]).message)
        if intent in {"create", "direct_search"} and state.get("attraction_result") is None:
            return action("call_agent", "搜索并筛选候选景点", target_agent="attraction_search")
        if intent == "direct_search":
            return action("finish", ActionResult.model_validate(state["attraction_result"]).message)
        if intent in {"create", "revise"}:
            if state.get("draft_itinerary") is None:
                return action("call_agent", "组合行程" if intent == "create" else "修改当前行程",
                              target_agent="planner")
            if state.get("validation_result") is None:
                return action("validate", "校验当前行程")
            if not state["validation_result"].passed:
                return action("fail", "行程未通过校验")
            if state.get("saved_version_no") is None:
                return action("save", "保存经校验的当前草稿")
            return action("finish", f"行程已保存为 v{state['saved_version_no']}")
        return action("fail", "该意图尚未支持")
