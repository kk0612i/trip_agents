"""离线工具夹具，仍经过实际 guarded_tool 入口。"""

import inspect

from langchain.tools import ToolRuntime, tool

from app.schemas.agent_schema import ActionResult
from app.tools.context import ToolContext
from app.tools.execution import guarded_tool
from app.schemas.tool_schema import ToolArguments


def query_tool(handler=None, *, name="query"):
    @tool(name, args_schema=ToolArguments, response_format="content_and_artifact")
    @guarded_tool(name)
    async def query(runtime: ToolRuntime[ToolContext]) -> ActionResult:
        """执行离线夹具查询。"""
        if handler is None:
            return ActionResult()
        result = handler(runtime.context.state, {})
        return await result if inspect.isawaitable(result) else result

    return query
