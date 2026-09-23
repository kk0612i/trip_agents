"""FastAPI 装配入口；显式管理日志及应用拥有的延迟资源。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.errors import register_error_handlers
from app.api.middleware import register_request_context
from app.api.auth_router import router as auth_router
from app.api.run_router import router as runs_router
from app.api.session_router import router as sessions_router
from app.api.trip_router import router as trips_router
from app.core.log import configure_logger, shutdown_logger, log_event
from app.core.resources import AppResources
from app.services.auth_service import AuthService
from app.services.run_service import RunService
from app.services.session_service import SessionService


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
        auth_service: 调用方注入的认证服务；None 时创建持有当前应用延迟会话工厂的骨架。

    Returns:
        注册所有 V1 路由的 FastAPI 实例。
    """
    # 当前独立应用实例；依赖保存在其 state 中，避免多个应用共享认证或资源状态。
    application = FastAPI(title="Trip Agents API", version="1.0.0", lifespan=lifespan)
    # 应用生命周期管理的资源容器；仅首次使用具体资源时才创建连接。
    application.state.resources = resources if resources is not None else AppResources()
    # 应用级认证依赖；默认只捕获会话工厂，避免启动或依赖注入时打开数据库连接。
    application.state.auth_service = auth_service if auth_service is not None else AuthService(
        lambda: application.state.resources.session_factory()
    )
    # 会话业务依赖；当前为占位服务，不保存实际会话数据。
    application.state.session_service = SessionService()
    # 运行管理依赖；当前为占位服务，不创建后台执行任务。
    application.state.run_service = RunService()

    register_request_context(application)
    register_error_handlers(application)

    application.include_router(sessions_router, prefix="/api/v1/sessions", tags=["sessions"])
    application.include_router(runs_router, prefix="/api/v1/runs", tags=["runs"])
    application.include_router(trips_router, prefix="/api/v1/trips", tags=["trips"])
    application.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])
    return application


# Uvicorn 导入的默认 ASGI 应用；导入阶段只装配依赖，资源释放由 lifespan 管理。
app = create_app()
