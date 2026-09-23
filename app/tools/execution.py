"""所有注册工具共用的执行检查、计数、串行化和结果封装。"""

from collections.abc import Awaitable, Callable
from typing import Any

from copy import deepcopy
from functools import wraps
from inspect import signature
import json
from time import perf_counter

from langchain.tools import ToolRuntime
from pydantic import ValidationError

from app.schemas.agent_schema import ActionResult
from app.tools.context import ToolContext
from app.tools.errors import RegistryError, ToolExecutionError
from app.core.log import log_event, safe_log_identifier


def guarded_tool(
    name: str,
    *,
    normalize: Callable[[ToolContext, dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Awaitable[ActionResult]]], Callable[..., Awaitable[tuple[str, dict[str, Any]]]]]:
    """为应用工具添加可信上下文、权限、额度和轨迹检查。

    Args:
        name: 注册表中的固定工具名，必须与被装饰工具一致。
        normalize: 可选的可信参数归一化函数，在消耗额度前执行。

    Returns:
        返回 content 与 artifact 的异步装饰器；直接 ainvoke 也不能绕过检查。
    """
    def decorate(func: Callable[..., Awaitable[ActionResult]]) -> Callable[..., Awaitable[tuple[str, dict[str, Any]]]]:
        parameters = signature(func)

        @wraps(func)
        async def guarded(*args: Any, **kwargs: Any) -> tuple[str, dict[str, Any]]:
            started_at = perf_counter()
            values = parameters.bind(*args, **kwargs)
            values.apply_defaults()
            runtime = values.arguments.pop("runtime", None)
            if not isinstance(runtime, ToolRuntime) or not isinstance(runtime.context, ToolContext):
                log_event("tool_rejected", level="WARNING", tool=name, status="rejected", error_type="RegistryError")
                raise RegistryError("工具必须由 AgentRuntime 建立调用作用域")
            context = runtime.context
            registry = context.registry
            registry.require_context(context)
            async with context.lock:
                # 关闭作用域或拒绝调用后，已经排队的协程不得继续访问外部服务。
                registry.require_context(context)
                if context.violation:
                    raise RegistryError(context.violation)
                trace = {"tool": name, "agent": context.agent_name, "status": "rejected",
                         "tool_call_id": runtime.tool_call_id}
                context.traces.append(trace)
                # 只取可信作用域编号及限长调用标识，不记录 arguments 或结果正文。
                log_fields = {"run_id": safe_log_identifier(context.state.get("run_id")),
                              "tool": name, "agent": safe_log_identifier(context.agent_name),
                              "tool_call_id": safe_log_identifier(runtime.tool_call_id)}
                try:
                    spec = registry.assert_allowed(context.agent_name, name, context.agents)
                    if spec.tool.coroutine is not guarded:
                        raise RegistryError("工具定义与当前注册表不一致")
                    if context.calls >= context.remaining:
                        raise RegistryError("达到最大工具调用次数")
                    arguments = dict(values.arguments)
                    if normalize is not None:
                        arguments = normalize(context, arguments)
                    arguments = spec.tool.tool_call_schema.model_validate(arguments).model_dump()
                except (RegistryError, ValidationError) as exc:
                    context.violation = str(exc) if isinstance(exc, RegistryError) else "工具参数格式不正确"
                    trace["error"] = context.violation
                    log_event("tool_rejected", level="WARNING", **log_fields, status="rejected",
                              call_count=context.calls, error_type=type(exc).__name__,
                              duration_ms=round((perf_counter() - started_at) * 1000, 2))
                    raise RegistryError(context.violation) from exc
                context.calls += 1
                trace.update(status="running", arguments=arguments)
                try:
                    if not spec.implemented:
                        result = ActionResult(status="unimplemented", message=f"工具 {name} 尚未实现")
                    else:
                        result = ActionResult.model_validate(await func(**arguments, runtime=runtime))
                except Exception as exc:
                    trace.update(status="failed", error=f"工具 {name} 执行异常：{type(exc).__name__}")
                    log_event("tool_completed", level="ERROR", **log_fields, status="failed",
                              call_count=context.calls, error_type=type(exc).__name__,
                              duration_ms=round((perf_counter() - started_at) * 1000, 2))
                    # 即使调用者记录完整 traceback，也不能暴露上游异常正文或 Key。
                    raise ToolExecutionError(trace["error"]) from None
                artifact = result.model_dump(mode="json")
                trace.update(status=result.status, result=deepcopy(artifact))
                content = (result.data if result.status == "completed" else
                           {"error": result.message or f"工具 {name} 尚未成功完成"})
                log_event("tool_completed", level="INFO" if result.status == "completed" else "WARNING",
                          **log_fields, status=result.status, call_count=context.calls,
                          duration_ms=round((perf_counter() - started_at) * 1000, 2))
                return json.dumps(content, ensure_ascii=False), artifact

        setattr(guarded, "tool_guard_name", name)
        return guarded
    return decorate
