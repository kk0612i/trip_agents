"""数据库认证服务骨架；注册、登录和令牌校验尚未实现。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CapabilityUnavailableError
from app.repository.auth_repository import UserRepository
from app.schemas.auth_schema import AuthResponse, Credentials, UserView


class AuthService:
    """使用请求级会话组织认证业务，事务由服务管理。

    会话由调用方关闭；服务及仓库不得跨并发任务共享。
    """

    def __init__(self, session: AsyncSession) -> None:
        """保存借用会话，并创建使用同一会话的用户仓库。

        Args:
            session: 当前请求或工作单元的异步会话，由调用方创建和关闭。
        """
        self.session = session
        self.user_repo = UserRepository(session)

    async def register(self, credentials: Credentials) -> AuthResponse:
        """预留注册业务；当前不开启事务、不写入用户或签发令牌。

        Args:
            credentials: 已通过公开契约校验的注册凭证；邮箱已规范化，密码保留空白。

        Returns:
            实现后返回令牌及公开用户信息；当前占位实现不会返回。

        Raises:
            CapabilityUnavailableError: 注册业务尚未实现，当前始终抛出。
        """
        raise CapabilityUnavailableError("用户注册")

    async def login(self, credentials: Credentials) -> AuthResponse:
        """预留登录业务；当前不查询用户、校验密码或签发令牌。

        Args:
            credentials: 已通过公开契约校验的登录凭证；格式合法不代表身份验证通过。

        Returns:
            实现后返回令牌及公开用户信息；当前占位实现不会返回。

        Raises:
            CapabilityUnavailableError: 登录业务尚未实现，当前始终抛出。
        """
        raise CapabilityUnavailableError("用户登录")

    async def authenticate(self, token: str) -> UserView:
        """预留令牌校验及用户读取；当前不接受任何令牌。

        Args:
            token: 从 Bearer 请求头提取的未验证令牌，不含 Bearer 前缀。

        Returns:
            实现后返回通过身份验证的公开用户信息；当前占位实现不会返回。

        Raises:
            CapabilityUnavailableError: 身份认证尚未实现，当前始终抛出。
        """
        # 认证未实现时必须明确拒绝，不能把客户端令牌内容直接当作可信身份。
        raise CapabilityUnavailableError("身份认证")
