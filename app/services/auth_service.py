from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import EmailAlreadyRegisteredError, AccountNotFoundError, InvalidPasswordError, \
    AuthenticationFailedError
from app.core.security import create_access_token, decode_access_token, hash_password, verify_password
from app.models import AppUser
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
        """
        async with self.session.begin():
            query_user = await self.user_repo.find_by_email(credentials.email)
            # 当前邮箱已注册
            if query_user:
                raise EmailAlreadyRegisteredError()

            salt, password_hash = hash_password(password=credentials.password)
            user_id = str(uuid4())
            app_user = AppUser(
                id=user_id,
                email=credentials.email,
                password_hash=password_hash,
                password_salt=salt,
                created_at=datetime.now(timezone.utc),
            )
            await self.user_repo.add(app_user)
        token = create_access_token(
            user_id=user_id
        )
        return AuthResponse(
            access_token=token,
            user=UserView(
                id=user_id,
                email=credentials.email
            )
        )

    async def login(self, credentials: Credentials) -> AuthResponse:
        """预留登录业务；当前不查询用户、校验密码或签发令牌。

        Args:
            credentials: 已通过公开契约校验的登录凭证；格式合法不代表身份验证通过。

        Returns:
            实现后返回令牌及公开用户信息；当前占位实现不会返回。

        Raises:
            stub_exc: stub
        """
        email = credentials.email
        password = credentials.password

        app_user = await self.user_repo.find_by_email(email)
        # 账号不存在
        if not app_user:
            raise AccountNotFoundError()

        # 密码无效
        if not verify_password(
                password=password,
                salt=app_user.password_salt,
                expected_hash=app_user.password_hash
        ):
            raise InvalidPasswordError()

        token = create_access_token(app_user.id)
        return AuthResponse(
            access_token=token,
            user=UserView(
                id=app_user.id,
                email=email
            )
        )


    async def authenticate(self, token: str) -> UserView:
        """预留令牌校验及用户读取；当前不接受任何令牌。

        Args:
            token: 从 Bearer 请求头提取的未验证令牌，不含 Bearer 前缀。

        Returns:
            实现后返回通过身份验证的公开用户信息；当前占位实现不会返回。

        Raises:
            stub_exc: 身份认证尚未实现，当前始终抛出。
        """
        user_id = decode_access_token(token)
        # 身份验证失败
        if not user_id:
            raise AuthenticationFailedError()
        user = await self.user_repo.find_by_id(user_id)

        if user is None:
            raise AuthenticationFailedError()

        return UserView(
            id=user_id,
            email=user.email
        )
