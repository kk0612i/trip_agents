"""请求编号及缓存策略的 HTTP 适配。"""

from uuid import uuid4
from time import perf_counter
from fastapi import FastAPI, Request, Response
from fastapi.routing import iter_route_contexts
from starlette.routing import Match
from starlette.middleware.base import RequestResponseEndpoint
from app.core.log import logger, log_event


def _route_template(request: Request) -> str:
    """从已匹配路由取得含 include_router 前缀的模板，不返回用户路径。"""
    route = request.scope.get("route")
    if route is not None:
        # 当前 FastAPI 保留原始子路由，公开 RouteContext 提供完整有效模板。
        for context in iter_route_contexts(request.app.routes):
            if context.original_route is route and context.matches(request.scope)[0] != Match.NONE:
                return context.path or "<unmatched>"
    return "<unmatched>"


def register_request_context(application: FastAPI) -> None:
    """为应用登记请求关联中间件。"""
    @application.middleware("http")
    async def request_context(request: Request, call_next: RequestResponseEndpoint) -> Response:
        """服务端生成请求编号，关联错误与日志，禁止缓存会话和运行响应。"""
        request.state.request_id = str(uuid4())
        with logger.contextualize(run_id="-", request_id=request.state.request_id):
            started_at = perf_counter()
            try:
                response = await call_next(request)
            except Exception as exc:
                # 仅记录摘要；完整异常栈留给服务器边界，避免重复且泄露参数。
                log_event("http_unhandled_error", level="ERROR", status="failed",
                          method=request.method, route=_route_template(request),
                          error_type=type(exc).__name__, duration_ms=round((perf_counter() - started_at) * 1000, 2))
                raise
            # 这里只统计收到响应头的耗时，不能代表未来 SSE 流结束时间。
            log_event("http_response_started", method=request.method,
                      route=_route_template(request),
                      status_code=response.status_code,
                      status="failed" if response.status_code >= 500 and response.status_code != 501 else (
                          "completed" if response.status_code < 400 else "rejected"),
                      duration_ms=round((perf_counter() - started_at) * 1000, 2))
        response.headers["X-Request-Id"] = request.state.request_id
        if request.url.path.startswith(("/api/v1/sessions", "/api/v1/runs")):
            response.headers["Cache-Control"] = "no-store"
        return response
