"""公开错误 DTO 的统一 HTTP 映射。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.core.errors import CapabilityUnavailableError
from app.core.log import log_event
from app.schemas.api_schema import ErrorResponse, PublicError


def register_error_handlers(application: FastAPI) -> None:
    """注册能力不可用及输入校验处理器。

    Args:
        application: 接收异常处理器的 FastAPI 应用，请求编号由中间件注入。
    """
    @application.exception_handler(CapabilityUnavailableError)
    async def unavailable(request: Request, exc: CapabilityUnavailableError) -> JSONResponse:
        """把业务占位异常映射为 501，不泄露内部请求或连接参数。

        Args:
            request: 当前 HTTP 请求，包含中间件生成的请求编号。
            exc: 服务或 Repository 抛出的能力不可用异常，能力名称可安全公开。

        Returns:
            携带统一错误码及请求编号的 JSON 501 响应。
        """
        log_event("capability_unavailable", level="WARNING", operation=exc.capability,
                  status="rejected", error_type=type(exc).__name__, code=exc.code)
        # 公开错误载荷；只携带受控业务信息，不包含请求体或数据库连接信息。
        payload = ErrorResponse(request_id=request.state.request_id,
                                error=PublicError(code=exc.code, message=str(exc)))
        return JSONResponse(status_code=501, content=payload.model_dump(mode="json"))

    @application.exception_handler(RequestValidationError)
    async def invalid_arguments(request: Request, exc: RequestValidationError) -> JSONResponse:
        """所有业务接口复用公开错误 DTO，不回传密码等原始输入。

        Args:
            request: 当前 HTTP 请求，包含中间件生成的请求编号。
            exc: FastAPI 请求解析或参数校验产生的异常，可能包含敏感原始输入。

        Returns:
            JSON 语法错误返回 400，其他参数校验错误返回 422，均使用公开错误结构。
        """
        log_event("request_validation_rejected", status="rejected", error_type=type(exc).__name__)
        # 根据框架错误类型区分 JSON 语法与字段校验，决定 HTTP 状态码。
        invalid_json = any(error["type"] == "json_invalid" for error in exc.errors())
        # 仅输出字段位置及固定说明，避免异常中的 input、ctx 或原始消息泄露密码。
        payload = ErrorResponse(request_id=request.state.request_id, error=PublicError(
            code="INVALID_JSON" if invalid_json else "INVALID_ARGUMENT",
            message="JSON 语法错误" if invalid_json else "请求参数不合法",
            details={"fields": [{"field": ".".join(map(str, error["loc"])),
                                 "message": "参数不符合接口约束"} for error in exc.errors()]},
        ))
        return JSONResponse(status_code=400 if invalid_json else 422, content=payload.model_dump(mode="json"))
