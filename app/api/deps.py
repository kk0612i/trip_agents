"""HTTP 依赖适配；占位接口不触发模型或数据库资源初始化。"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Header, Query, Request
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
    """借用独立短会话；提交由业务工作单元负责，退出时关闭。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Yields:
        当前请求借用的独立会话；退出依赖时关闭，未提交事务回滚。
    """
    async with get_resources(request).session_factory() as session:
        yield session


def get_auth_service(request: Request) -> AuthService:
    """取得当前应用的数据库认证骨架，不打开数据库会话。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Returns:
        持有延迟数据库会话工厂的认证服务骨架。
    """
    return request.app.state.auth_service


def get_session_service(request: Request) -> SessionService:
    """取得会话骨架服务；正式身份依赖仍待接入。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Returns:
        尚未接入真实会话数据的业务骨架。
    """
    return request.app.state.session_service


def get_run_service(request: Request) -> RunService:
    """取得运行骨架服务，不创建后台任务。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Returns:
        尚未接入调度、存储及事件推送的业务骨架。
    """
    return request.app.state.run_service


def get_trip_service(request: Request) -> TripService:
    """只传递延迟会话工厂；公开占位方法不访问数据库。

    Args:
        request: 当前 HTTP 请求，用于取得应用实例拥有的依赖。

    Returns:
        只捕获会话工厂的旅行服务，不持有活动 Session。
    """
    return TripService(lambda: get_resources(request).session_factory())


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
