"""注册登录 HTTP 骨架；未实现的认证能力明确返回 501。"""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_auth_service
from app.schemas.api_schema import ErrorResponse
from app.schemas.auth_schema import AuthResponse, Credentials
from app.services.auth_service import AuthService

# 注册登录路由集合；501 文档对应服务占位异常，由统一错误处理器生成响应。
router = APIRouter(responses={501: {"model": ErrorResponse, "description": "认证尚未实现"}})
# 认证服务的依赖类型别名，由当前应用提供，不表示请求已通过身份验证。
AuthDep = Annotated[AuthService, Depends(get_auth_service)]


@router.post("/register", response_model=AuthResponse, status_code=201)
async def register(credentials: Credentials, service: AuthDep) -> AuthResponse:
    """转交数据库注册服务骨架；当前不创建用户。

    Args:
        credentials: FastAPI 按正式认证契约校验的注册凭证，密码保留首尾空白。
        service: 当前应用注入的认证服务，不由路由创建或关闭数据库会话。

    Returns:
        业务实现后返回注册令牌及公开用户信息；当前占位服务不会返回成功结果。

    Raises:
        CapabilityUnavailableError: 默认服务尚未实现注册，由统一处理器转换为 501。
    """
    return await service.register(credentials)


@router.post("/login", response_model=AuthResponse)
async def login(credentials: Credentials, service: AuthDep) -> AuthResponse:
    """转交数据库登录服务骨架；当前不签发令牌。

    Args:
        credentials: FastAPI 按正式认证契约校验的登录凭证，尚未验证账号密码。
        service: 当前应用注入的认证服务，不由路由创建或关闭数据库会话。

    Returns:
        业务实现后返回登录令牌及公开用户信息；当前占位服务不会返回成功结果。

    Raises:
        CapabilityUnavailableError: 默认服务尚未实现登录，由统一处理器转换为 501。
    """
    return await service.login(credentials)
