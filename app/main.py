"""FastAPI 装配入口；显式管理日志及应用拥有的延迟资源。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Depends

from app.api.deps import get_auth_service, get_current_user
from app.api.errors import register_error_handlers
from app.api.middleware import register_request_context
from app.api.auth_router import router as auth_router
from app.api.run_router import router as runs_router
from app.api.session_router import router as sessions_router
from app.api.trip_router import router as trips_router
from app.core.log import configure_logger, shutdown_logger, log_event
from app.core.resources import AppResources
from app.services.auth_service import AuthService


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动日志，退出时释放本应用容器拥有的资源；不提前连接外部服务。"""
    try:
        try:
            configure_logger()
        except Exception as exc:
            log_event("application_start_failed", level="CRITICAL", status="failed", error_type=type(exc).__name__)
            raise
        log_event("application_started", status="completed", resources="lazy")
        yield
    finally:
        try:
            await app.state.resources.aclose()
            log_event("application_stopped", status="completed")
        finally:
            await shutdown_logger()


def create_app(*, resources: AppResources | None = None,
               auth_service: AuthService | None = None) -> FastAPI:
    """创建独立应用，支持测试注入，模块导入不会打开文件或网络连接。

    Args:
        resources: 交由本应用关闭的资源容器；None 时创建独立容器。
            容器内由外部注入的对象仍归调用方管理。
        auth_service: 测试用认证服务替身，通过依赖覆盖注入，不应传入绑定真实会话的共享服务。
            None 时由 HTTP 依赖为每个请求创建独立认证服务。

    Returns:
        注册所有 V1 路由的 FastAPI 实例。
    """
    # 当前独立应用实例；依赖保存在其 state 中，避免多个应用共享认证或资源状态。
    application = FastAPI(title="Trip Agents API", version="1.0.0", lifespan=lifespan)
    # 应用生命周期管理的资源容器；仅首次使用具体资源时才创建连接。
    application.state.resources = resources if resources is not None else AppResources()
    # 真实服务由请求依赖创建，避免应用级实例共享数据库会话。
    if auth_service is not None:
        application.dependency_overrides[get_auth_service] = lambda: auth_service

    register_request_context(application)
    register_error_handlers(application)

    protected = [Depends(get_current_user)]
    application.include_router(
        sessions_router,
        prefix="/api/v1/sessions",
        tags=["sessions"],
        dependencies=protected,
    )
    application.include_router(
        runs_router,
        prefix="/api/v1/runs",
        tags=["runs"],
        dependencies=protected,
    )
    application.include_router(
        trips_router,
        prefix="/api/v1/trips",
        tags=["trips"],
        dependencies=protected,
    )

    # 注册、登录保持公开。
    application.include_router(
        auth_router,
        prefix="/api/v1/auth",
        tags=["auth"],
    )
    return application


# Uvicorn 导入的默认 ASGI 应用；导入阶段只装配依赖，资源释放由 lifespan 管理。
app = create_app()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)