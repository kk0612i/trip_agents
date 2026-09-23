"""会话与消息入口骨架；尚不接收任务或读取真实会话。"""

from typing import Annotated
from fastapi import APIRouter, Depends

from app.api.deps import get_run_service, get_session_service, page_query
from app.schemas.api_schema import (ErrorResponse, Page, PageQuery, RunReceipt, RunSubmission,
                                    RunView, SessionCreate, SessionSummary, SessionView, UUIDString)
from app.services.run_service import RunService
from app.services.session_service import SessionService

router = APIRouter(responses={501: {"model": ErrorResponse, "description": "能力尚未实现"}})
SessionDep = Annotated[SessionService, Depends(get_session_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]
PageDep = Annotated[PageQuery, Depends(page_query)]


@router.post("", response_model=SessionView, status_code=201)
async def create_session(payload: SessionCreate, service: SessionDep) -> SessionView:
    """创建会话待实现；合法请求返回 501，不创建空旅行。

    Args:
        payload: 已校验的业务请求，不包含可信的服务端身份。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.create_session(payload)


@router.get("", response_model=Page[SessionSummary])
async def list_sessions(service: SessionDep, query: PageDep) -> Page[SessionSummary]:
    """会话列表待实现；分页参数先按公开契约校验。

    Args:
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。
    """
    return await service.list_sessions(query)


@router.get("/{session_id}", response_model=SessionView)
async def get_session(session_id: UUIDString, service: SessionDep) -> SessionView:
    """会话读取待实现；尚未接入正式身份与归属验证。

    Args:
        session_id: 公开会话 UUID；访问前仍需验证归属。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.get_session(session_id)


@router.post("/{session_id}/runs", response_model=RunReceipt, status_code=202)
async def submit_run(session_id: UUIDString, payload: RunSubmission, service: RunDep) -> RunReceipt:
    """运行受理待实现；不写入幂等记录、不调度后台任务。

    Args:
        session_id: 公开会话 UUID；访问前仍需验证归属。
        payload: 已校验的业务请求，不包含可信的服务端身份。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.submit_run(session_id, payload)


@router.get("/{session_id}/runs", response_model=Page[RunView])
async def list_runs(session_id: UUIDString, service: RunDep, query: PageDep) -> Page[RunView]:
    """运行历史待实现；不会使用虚构运行填充响应。

    Args:
        session_id: 公开会话 UUID；访问前仍需验证归属。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。
    """
    return await service.list_runs(session_id, query)
