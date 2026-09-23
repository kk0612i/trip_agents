"""HTTP 依赖适配；按请求创建数据库会话和服务，事务由服务管理。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.resources import AppResources
from app.schemas.api_schema import PageQuery, UUIDString
from app.services.auth_service import AuthService
from app.services.run_service import RunService
from app.services.session_service import SessionService
from app.services.trip_service import TripService


def get_resources(request: Request) -> AppResources:
    """取得当前应用拥有的容器，避免跨应用复用全局连接池。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Returns:
        当前应用的延迟资源容器；不会在此初始化外部服务。
    """
    return request.app.state.resources


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    """创建请求级会话；服务负责事务，依赖清理时关闭会话。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Yields:
        当前请求借用的独立会话；退出依赖时关闭，未提交事务回滚。
    """
    async with get_resources(request).session_factory() as session:
        yield session


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthService:
    """创建当前请求的认证服务，并注入数据库会话。

    Args:
        session: get_db 提供的请求级会话，关闭由依赖清理负责。

    Returns:
        持有当前会话及用户仓库的认证服务。
    """
    return AuthService(session)


def get_session_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> SessionService:
    """创建当前请求的对话服务，并注入数据库会话。

    Args:
        session: get_db 提供的请求级会话，关闭由依赖清理负责。

    Returns:
        持有当前会话及对话仓库的业务服务。
    """
    return SessionService(session)


def get_run_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> RunService:
    """创建当前请求的运行服务，并注入数据库会话。

    Args:
        session: get_db 提供的请求级会话，关闭由依赖清理负责。

    Returns:
        持有当前会话及运行仓库的业务服务。
    """
    return RunService(session)


def get_trip_service(
    session: Annotated[AsyncSession, Depends(get_db)],
) -> TripService:
    """创建当前请求的旅行服务，并注入数据库会话。

    Args:
        session: get_db 提供的请求级会话，关闭由依赖清理负责。

    Returns:
        持有当前会话及旅行仓库的业务服务。
    """
    return TripService(session)


def page_query(
    limit: Annotated[str, Query(pattern=r"^[0-9]+$", max_length=3)] = "20",
    cursor: Annotated[str | None, Query(min_length=1)] = None,
) -> PageQuery:
    """将 HTTP 十进制文本转为严格 DTO 整数，拒绝空白游标及越界页长。

    Args:
        limit: HTTP 查询中的十进制页长文本，解析后必须处于 1 到 100。
        cursor: 服务端产生的不透明分页游标；None 表示首页。

    Returns:
        完成文本解析和范围校验的严格分页 DTO。
    """
    try:
        return PageQuery(limit=int(limit), cursor=cursor)
    except ValidationError as exc:
        errors = [{**error, "loc": ("query", *error["loc"])} for error in exc.errors()]
        raise RequestValidationError(errors) from exc


def event_cursor(
    run_id: UUIDString,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> str | None:
    """检查重连编号属于本次运行；这里只校验文本，不读取或发送事件。

    Args:
        run_id: 公开运行 UUID，不等同于 Graph 内部运行编号。
        last_event_id: Last-Event-ID 请求头；None 表示从首个保留事件开始。

    Returns:
        已校验归属及序号的事件编号；未提供时为 None。
    """
    if last_event_id is None:
        return None
    try:
        event_run, seq = last_event_id.rsplit(":", 1)
        if TypeAdapter(UUIDString).validate_python(event_run) != run_id:
            raise ValueError("事件编号属于其他运行")
        if not seq.isascii() or not seq.isdecimal() or int(seq) < 1:
            raise ValueError("事件序号必须为正整数")
    except (ValueError, ValidationError) as exc:
        raise RequestValidationError([{
            "type": "value_error", "loc": ("header", "Last-Event-ID"),
            "msg": "事件编号必须为当前运行 UUID:正整数序号", "input": last_event_id,
        }]) from exc
    return last_event_id
