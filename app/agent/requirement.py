"""需求解析 Agent：返回需求补丁，合并与缺失字段判断归主管执行层。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.agent.supervisor.supervisor_state import AgentRunState
from app.schemas.agent_schema import ActionResult
from app.schemas.requirement_schema import MinimalParsedRequest


if TYPE_CHECKING:
    from app.services.llm_service import LLMService


@dataclass
class RequirementAgent:
    """解析需求和意图，不生成完整行程。"""

    llm_service: LLMService | None = None  # 外部拥有的模型服务；None 时使用离线规则。
    name: str = "requirement"
    description: str = "解析用户需求和意图，不生成行程"
    allowed_tools: frozenset[str] = frozenset()

    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """解析当前消息并返回补丁，不修改调用方状态。

        Args:
            state: 当前运行状态的独立快照。
            instruction: 主管下达的需求解析指令。

        Returns:
            包含最小需求模型 JSON 的成功结果；模型调用异常继续向执行层传播。
        """
        previous = state.get("trip_request")
        if self.llm_service is not None:
            parsed = await self.llm_service.parse_minimal_request(
                state["user_message"], previous, state.get("intent"),
                instruction=instruction, current_itinerary=state.get("current_itinerary"))
        else:
            parsed = offline_requirement(state["user_message"], state.get("intent"))
        return ActionResult(data=parsed.model_dump(mode="json"))


def offline_requirement(message: str, previous_intent: str | None) -> MinimalParsedRequest:
    """有限规则仅供无模型时演示：明确城市、1~14 天和常见意图。

    Args:
        message: 当前用户消息。
        previous_intent: 上一轮意图；仅补充天数时允许沿用创建或搜索意图。

    Returns:
        可由运行器合并的需求补丁，无法确认的城市和天数保持 None。
    """
    text = message.strip()
    match = re.search(r"(?:去|到|在)([\u4e00-\u9fff]{2,8}?)(?:玩|旅游|旅行|的|[，,。 ]|$)", text)
    destination = match.group(1) if match else None
    day_match = re.search(r"([0-9]{1,2}|[一二两三四五六七八九十]{1,3})\s*(?:天|日)", text)
    days = None
    if day_match:
        token = day_match.group(1)
        chinese = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
                   "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
                   "十一": 11, "十二": 12, "十三": 13, "十四": 14}
        days = int(token) if token.isdigit() else chinese.get(token)
        if days is not None and not 1 <= days <= 14:
            days = None
    if any(word in text for word in ("修改", "调整", "删掉", "替换")):
        intent = "revise"
    elif any(word in text for word in ("有什么历史", "介绍一下", "知识", "为什么")):
        intent = "knowledge"
    elif any(word in text for word in ("找", "搜索", "推荐景点")) and not days:
        intent = "direct_search"
    elif destination or days or any(word in text for word in ("规划", "旅行", "旅游")):
        intent = previous_intent if days and not destination and previous_intent in {"create", "direct_search"} else "create"
    else:
        intent = "other"
    return MinimalParsedRequest(
        intent=intent, trip_request={"destination": destination, "days": days},
        search_keywords=[word for word in ("博物馆", "历史", "美食", "自然") if word in text])
