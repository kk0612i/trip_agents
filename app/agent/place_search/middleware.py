"""景点搜索模型协议校验与工具错误适配。"""

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command
from pydantic import ValidationError

from app.schemas.place_search_schema import _SearchDecision
from app.tools.errors import ToolExecutionError
from app.tools.registry import ToolRegistry


class _SearchProtocolError(RuntimeError):
    """只携带可展示的协议错误，不回显模型或上游异常。"""


class _SearchProtocolMiddleware(AgentMiddleware):
    """框架负责循环；这里保留非法调用立即失败的业务契约。"""

    def __init__(self, registry: ToolRegistry) -> None:
        """保存当前运行使用的工具注册表，不持有独立资源。"""
        self.registry = registry

    async def awrap_model_call(
        self, request: ModelRequest, handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """限制模型只能搜索或提交筛选结果，并校验搜索参数。"""
        response = await handler(request)
        message = response.result[0]
        if not isinstance(message, AIMessage) or message.invalid_tool_calls:
            raise _SearchProtocolError("模型工具调用格式不正确")
        if not message.tool_calls:
            raise _SearchProtocolError("模型输出的景点筛选结果格式不正确")
        ids = set()
        for call in message.tool_calls:
            if call["name"] not in {"search_attractions", "get_place_detail", _SearchDecision.__name__}:
                raise _SearchProtocolError("模型请求了未开放的工具")
            if not call.get("id") or call["id"] in ids:
                raise _SearchProtocolError("模型工具调用缺少调用编号或编号重复")
            ids.add(call["id"])
            if call["name"] in {"search_attractions", "get_place_detail"}:
                try:
                    self.registry.get(call["name"]).args_schema.model_validate({**call["args"], "runtime": request.runtime})
                except ValidationError as exc:
                    raise _SearchProtocolError("模型搜索参数格式不正确") from exc
        # 最终筛选必须基于已经取得的证据，不能与新搜索在同一批提交。
        if any(call["name"] == _SearchDecision.__name__ for call in message.tool_calls) and len(message.tool_calls) != 1:
            raise _SearchProtocolError("搜索与最终筛选不能同时提交")
        return response

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """将工具失败转换为模型可处理的错误，同时保留执行账本。"""
        try:
            message = await handler(request)
        except ToolExecutionError:
            # 执行层已留下失败账本；模型可以在剩余额度内换关键词补搜。
            return ToolMessage(content='{"error": "高德搜索失败"}',
                               tool_call_id=request.tool_call["id"], status="error")
        return message


