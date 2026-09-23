"""运行状态与 SSE 占位入口，建立流式连接前明确拒绝未实现能力。"""

from typing import Annotated
from fastapi import APIRouter, Depends, Response

from app.api.deps import event_cursor, get_run_service
from app.core.errors import CapabilityUnavailableError
from app.schemas.api_schema import ErrorResponse, RunView, UUIDString
from app.services.run_service import RunService

router = APIRouter(responses={501: {"model": ErrorResponse, "description": "能力尚未实现"}})
RunDep = Annotated[RunService, Depends(get_run_service)]


@router.get("/{run_id}", response_model=RunView)
async def get_run(run_id: UUIDString, service: RunDep) -> RunView:
    """运行状态读取待实现；当前固定报告能力不可用。

    Args:
        run_id: 公开运行 UUID，不等同于 Graph 内部运行编号。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.get_run(run_id)


@router.get("/{run_id}/events", response_class=Response,
            responses={200: {"description": "待实现的事件流", "content": {"text/event-stream": {}}}})
async def events(run_id: UUIDString, service: RunDep,
                 after_event_id: Annotated[str | None, Depends(event_cursor)]) -> Response:
    """事件订阅待实现；在任何 StreamingResponse 创建前返回普通 JSON 错误。

    Args:
        run_id: 公开运行 UUID，不等同于 Graph 内部运行编号。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        after_event_id: 可选的上次消费事件编号；None 表示未提供重放起点。

    Raises:
        CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
    """
    await service.subscribe_events(run_id, after_event_id)
    # 即使替身返回迭代器，本阶段也没有推送实现，不能误报成功。
    raise CapabilityUnavailableError("运行事件推送")
