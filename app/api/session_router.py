"""会话与消息入口；创建和列表已接入，其他业务保留占位。"""

from typing import Annotated
from fastapi import APIRouter, Depends, Response

from app.api.deps import get_run_service, get_session_service, page_query, CurrentUserDep
from app.schemas.api_schema import (ErrorResponse, Page, PageQuery, RunReceipt, RunSubmission,
                                    RunView, SessionCreate, SessionSummary, SessionView, UUIDString)
from app.services.run_service import RunService
from app.services.session_service import SessionService

router = APIRouter()
SessionDep = Annotated[SessionService, Depends(get_session_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]
PageDep = Annotated[PageQuery, Depends(page_query)]


@router.post("", response_model=SessionView, status_code=201, responses={
    401: {"model": ErrorResponse, "description": "身份认证失败"},
    404: {"model": ErrorResponse, "description": "旅行不存在或不属于当前用户"},
    409: {"model": ErrorResponse, "description": "旅行没有当前正式版本"},
})
async def create_session(
    payload: SessionCreate, service: SessionDep,
    current_user: CurrentUserDep, response: Response,
) -> SessionView:
    """创建当前用户的会话，返回新资源的位置。

    Args:
        payload: 已校验的业务请求，不包含可信的服务端身份。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        current_user: 服务端认证的用户，客户端不能指定会话所有者。
        response: 用于设置新建会话的 Location 响应头。
    """
    # 服务返回时写入已提交，才向客户端返回成功及资源地址。
    view = await service.create_session(payload=payload, user_id=current_user.id)
    response.headers["Location"] = f"/api/v1/sessions/{view.session_id}"
    return view


@router.get("", response_model=Page[SessionSummary])
async def list_sessions(
        service: SessionDep,
        query: PageDep,
        current_user: CurrentUserDep,
) -> Page[SessionSummary]:
    """查询当前登录用户的会话摘要，按游标加载后续页。

    Args:
        service: 外部注入的业务服务；路由不拥有其连接或存储。
        query: 已校验的页长与原始游标，由服务验证并解码。
        current_user: 服务端认证的用户，列表不接受客户端指定所有者。
    """
    return await service.list_sessions(query=query, user_id=current_user.id)


@router.get("/{session_id}", response_model=SessionView)
async def get_session(
        session_id: UUIDString,
        service: SessionDep,
        current_user: CurrentUserDep,
) -> SessionView:
    """会话读取待实现；尚未接入正式身份与归属验证。

    Args:
        session_id: 公开会话 UUID；访问前仍需验证归属。
        service: 外部注入的业务服务；路由不拥有其连接或存储。
    """
    return await service.get_session(
        session_id=session_id,
        user_id=current_user.id,
    )


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
