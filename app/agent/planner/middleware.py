"""Planner 模型和工具协议中间件，不决定工具调用顺序。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command
from pydantic import ValidationError

from app.agent.planner.errors import PlanningError
from app.schemas.agent_schema import ActionResult
from app.schemas.trip_schema import PlaceCandidate
from app.tools.context import ToolContext
from app.tools.errors import ToolExecutionError
from app.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from loguru import Logger


class PlannerMiddleware(AgentMiddleware):
    """校验模型协议、绑定地点证据并记录可观测动作，不规定工具顺序。"""

    def __init__(
        self, registry: ToolRegistry, places: Mapping[str, PlaceCandidate], log: Logger,
    ) -> None:
        """保存当前规划作用域的依赖，不跨次运行共享调用编号。

        Args:
            registry: 负责权限、额度和真实执行轨迹的工具注册表。
            places: 来自搜索或已有行程的可信地点证据。
            log: 绑定本轮运行编号的日志对象。
        """
        self.registry, self.places, self.log = registry, places, log
        self.round = 0
        self.call_ids: set[str] = set()

    async def awrap_model_call(
        self,
        request: ModelRequest[ToolContext],
        handler: Callable[[ModelRequest[ToolContext]], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """执行一次模型请求，并约束可见工具协议。

        Args:
            request: LangChain 提供的模型请求。
            handler: 后续中间件和模型执行入口。

        Returns:
            已通过工具名称、调用编号及最终提交互斥校验的模型响应。

        Raises:
            PlanningError: 模型未遵循既定工具协议。
        """
        self.round += 1
        self.log.debug("event=planner_model_started round={}", self.round)
        # 使用 auto 自主选择；部分推理模型拒绝强制 tool_choice=required。
        started_at = perf_counter()
        response = await handler(request.override(tool_choice="auto"))
        message = response.result[0]
        if not isinstance(message, AIMessage) or message.invalid_tool_calls or not message.tool_calls:
            raise PlanningError("模型没有提交有效的工具调用或结构化规划结果")
        names = []
        for call in message.tool_calls:
            name = call["name"]
            if name not in {"calculate_route", "estimate_itinerary_cost", "_PlannerResult"}:
                raise PlanningError("模型请求了未开放的规划工具")
            call_id = call.get("id")
            if not call_id or call_id in self.call_ids:
                raise PlanningError("模型工具调用编号缺失或重复")
            self.call_ids.add(call_id)
            names.append(name)
        if "_PlannerResult" in names and len(names) != 1:
            raise PlanningError("最终结果不能与新工具查询同时提交")
        # 记录可观察的动作，不记录提示词、原始输出或模型内部思考。
        self.log.bind(event="planner_model_completed", status="completed", round=self.round,
                      duration_ms=round((perf_counter() - started_at) * 1000, 2)).info(
            "event=planner_model_completed round={} actions={} duration_ms={:.2f}",
            self.round, ",".join(names), (perf_counter() - started_at) * 1000)
        return response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        """绑定可信地点与价格后执行工具，并向模型返回可引用的调用编号。

        Args:
            request: 模型发出的工具调用。
            handler: 统一工具执行入口；内部提交直接转交处理。

        Returns:
            工具结果；可恢复的执行失败转为错误消息供模型调整方案。

        Raises:
            PlanningError: 参数或地点、价格证据不符合约束。
        """
        name = request.tool_call["name"]
        if name == "_PlannerResult":
            return await handler(request)
        args = dict(request.tool_call["args"])
        if "runtime" in args:
            raise PlanningError("模型不能指定工具运行上下文")
        try:
            parsed = self.registry.get(name).args_schema.model_validate({**args, "runtime": None})
        except ValidationError:
            raise PlanningError("模型规划工具参数格式不正确") from None
        args = parsed.model_dump()
        if name == "calculate_route":
            for key in ("origin", "destination"):
                place = self.places.get(args[key].get("place_id"))
                if place is None:
                    raise PlanningError("路线查询必须使用候选或已有行程中的地点编号")
                # 模型决定查哪两个点；坐标和身份由输入证据提供。
                args[key] = place.model_dump(include={"place_id", "name", "address", "longitude", "latitude"})
        elif name == "estimate_itinerary_cost":
            if not args["items"]:
                raise PlanningError("费用工具必须显式提交本次规划的项目明细")
            for item in args["items"]:
                place = self.places.get(item["description"])
                if (item["category"] != "tickets" or place is None
                        or item["amount"] != place.estimated_cost):
                    raise PlanningError("费用明细必须使用地点编号及其已知价格，未知价格必须为 null")
        try:
            message = await handler(request.override(tool_call={**request.tool_call, "args": args}))
        except ToolExecutionError:
            # 将失败交还模型，由其选择重试、改方式或明确失败；仍受统一调用额度约束。
            return ToolMessage(content='{"error":"工具执行失败，可在剩余额度内调整方案"}',
                               tool_call_id=request.tool_call["id"], name=name, status="error")
        status = ActionResult.model_validate(message.artifact).status
        # 有些网关不会把协议层调用 ID 展示给模型，在可见结果中显式提供引用编号。
        content = json.loads(message.content)
        return message.model_copy(update={"content": json.dumps({
            **content, "tool_call_id": request.tool_call["id"],
        }, ensure_ascii=False)})
