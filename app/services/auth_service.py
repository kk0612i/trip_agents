"""数据库认证服务骨架；注册、登录和令牌校验尚未实现。"""

from app.core.db import SessionFactory
from app.core.errors import CapabilityUnavailableError
from app.schemas.auth_schema import AuthResponse, Credentials, UserView


class AuthService:
    """仅保存数据库会话工厂，不保存内存用户或随机签名密钥。"""

    def __init__(self, session_factory: SessionFactory) -> None:
        """保存延迟工厂，预留短事务及 UserRepository 的装配边界。

        Args:
            session_factory: 应用资源容器提供的会话工厂，每次调用创建独立工作单元。
        """
        # 数据库会话的创建入口；只保存工厂，避免共享活动会话或构造时连接数据库。
        self.session_factory = session_factory

    async def register(self, credentials: Credentials) -> AuthResponse:
        """预留注册业务；当前不打开数据库会话、不写入用户或签发令牌。

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
