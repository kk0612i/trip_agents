"""景点搜索 Agent 的图组装与单轮执行入口。"""

from __future__ import annotations

import json

from langchain.agents import create_agent
from langchain.agents.middleware import ModelCallLimitMiddleware
from langchain.agents.middleware.model_call_limit import ModelCallLimitExceededError
from langchain.agents.structured_output import StructuredOutputError, ToolStrategy
from langchain_core.messages import HumanMessage
from langchain_core.language_models import BaseChatModel
from pydantic import TypeAdapter, ValidationError

from app.schemas.place_search_schema import _SearchDecision

from app.prompts.place_search_prompt import PLACE_SEARCH_AGENT_SYSTEM_PROMPT
from app.tools.context import ToolContext
from app.tools.errors import RegistryError
from app.tools.registry import ToolRegistry
from app.schemas.agent_schema import ActionResult
from app.schemas.place_search_schema import PlaceSearchResult
from app.agent.supervisor.supervisor_state import AgentRunState
from app.agent.place_search.checks import collect_search_evidence, finalize_search_result
from app.agent.place_search.middleware import _SearchProtocolError, _SearchProtocolMiddleware
from app.schemas.trip_schema import TripRequest


class PlaceSearchAgent:
    """搜索并筛选候选景点，不负责安排每日行程。

    关键词、补搜和筛选由模型决定；城市范围、搜索次数和推荐事实边界由代码约束。
    搜索结果只来自高德 POI 证据，门票、营业时间等基础 POI 未提供的属性不会被推断。
    """

    name = "attraction_search"
    description = "搜索和筛选候选景点，不安排每日行程"
    allowed_tools = frozenset({"search_attractions", "get_place_detail"})

    def __init__(self, llm: BaseChatModel | None, tools: ToolRegistry, *, max_tool_calls: int = 3) -> None:
        """初始化景点搜索 Agent。

        Args:
            llm: 用于决定搜索关键词和候选筛选结果的聊天模型。
            tools: 提供工具注册、权限检查和本轮执行账本的工具注册表。
            max_tool_calls: 本次运行搜索与详情工具共用的调用次数上限。

        Raises:
            ValueError: `max_tool_calls` 不是正数时抛出。
        """
        if max_tool_calls < 1:
            raise ValueError("搜索工具调用上限必须大于 0")
        self.llm = llm
        self.tools = tools
        self.max_tool_calls = max_tool_calls

    async def run(self, state: AgentRunState, instruction: str) -> ActionResult:
        """根据旅行需求搜索并筛选景点。

        Args:
            state: 包含 `trip_request` 和可选 `search_keywords` 的 Agent 状态。
            instruction: 本轮搜索的补充指令，会原样交给模型。

        Returns:
            返回统一的 `ActionResult`。成功结果包含候选、推荐理由、未核验条件和搜索轨迹；
            失败结果仍保留本轮已经执行的搜索轨迹。

        Side effects:
            通过 `ToolRegistry` 调用高德搜索，并将调用记录写入当前 `ToolContext` 账本。
        """
        result = PlaceSearchResult()

        def failed(message: str) -> ActionResult:
            # 失败仍返回统一结果和已执行轨迹，未筛选的候选不会作为推荐输出。
            return ActionResult(status="failed", message=message, data=result.model_dump(mode="json"))

        if self.llm is None:
            return ActionResult(status="unimplemented", message="景点筛选需要注入 LLMService")
        try:
            request = TripRequest.model_validate(state.get("trip_request") or {})
            search_keywords = TypeAdapter(list[str]).validate_python(state.get("search_keywords", []))
        except ValidationError:
            return failed("景点搜索需求或关键词格式不正确")
        destination = (request.destination or "").strip()
        if not destination:
            return failed("景点搜索缺少目的地")
        conditions = [*request.preferences, *request.constraints]

        context = None
        # 只消费本次运行新增的账本记录，避免重复读取上游 Agent 的搜索结果。
        trace_start = 0
        failure = None
        call_budget = self.max_tool_calls
        try:
            context = self.tools.current_context()
            trace_start = len(context.traces)
            # 在统一执行入口限制搜索和详情的合计次数，包括同批并行请求。
            call_budget = min(self.max_tool_calls, context.remaining - context.calls)
            context.remaining = context.calls + call_budget
            available_tools = self.tools.tools_for(self.name, names=("search_attractions", "get_place_detail"))
            agent = create_agent(
                model=self.llm, tools=available_tools, context_schema=ToolContext,
                system_prompt=PLACE_SEARCH_AGENT_SYSTEM_PROMPT,
                response_format=ToolStrategy(_SearchDecision, handle_errors=False),
                middleware=[
                    _SearchProtocolMiddleware(self.tools),
                    ModelCallLimitMiddleware(run_limit=max(1, call_budget + 1), exit_behavior="error"),
                ],
                checkpointer=False,
            )
            output = await agent.ainvoke({"messages": [HumanMessage(content=json.dumps({
                "request": request.model_dump(mode="json"),
                "search_keywords": search_keywords,
                "instruction": instruction,
                "max_tool_calls": call_budget,
                "conditions": list(enumerate(conditions)),
            }, ensure_ascii=False))]}, config={"recursion_limit": 50}, context=context)
            decision = output["structured_response"]
        except (RegistryError, _SearchProtocolError) as exc:
            failure = str(exc)
        except ModelCallLimitExceededError:
            failure = "搜索工具调用已达到上限，模型未完成筛选"
        except StructuredOutputError:
            failure = "模型输出的景点筛选结果格式不正确"
        except Exception:
            failure = "调用景点搜索模型失败，模型可能无法绑定工具"

        # 即使模型或权限检查失败，也从本轮账本保留已执行的搜索证据和尝试次数。
        records = context.traces[trace_start:] if context is not None else []
        candidates, evidence = collect_search_evidence(records, result, call_budget)
        if failure:
            return failed(failure)
        return finalize_search_result(result, decision, request, conditions, candidates, evidence, call_budget)
