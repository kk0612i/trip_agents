"""密码哈希与访问令牌的无状态安全工具。"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from jwt.exceptions import InvalidTokenError

from app.core.config import get_settings

PASSWORD_HASH_ALGORITHM = "sha256"
PASSWORD_HASH_ITERATIONS = 240_000
PASSWORD_SALT_BYTES = 16
PASSWORD_HASH_BYTES = 32
ACCESS_TOKEN_TTL = timedelta(days=7)
JWT_ALGORITHM = "HS256"
JWT_ISSUER = "trip-agents"


def hash_password(password: str) -> tuple[bytes, bytes]:
    """用随机盐计算 PBKDF2-SHA256 摘要，返回 (salt, password_hash)。"""
    salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
    password_hash = hashlib.pbkdf2_hmac(
        PASSWORD_HASH_ALGORITHM,
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
        dklen=PASSWORD_HASH_BYTES,
    )
    return salt, password_hash


def verify_password(password: str, salt: bytes, expected_hash: bytes) -> bool:
    """常量时间比较密码摘要；材料格式异常时安全返回 False。"""
    if len(salt) != PASSWORD_SALT_BYTES or len(expected_hash) != PASSWORD_HASH_BYTES:
        return False
    candidate_hash = hashlib.pbkdf2_hmac(
        PASSWORD_HASH_ALGORITHM,
        password.encode("utf-8"),
        salt,
        PASSWORD_HASH_ITERATIONS,
        dklen=PASSWORD_HASH_BYTES,
    )
    return hmac.compare_digest(candidate_hash, expected_hash)


def create_access_token(user_id: str) -> str:
    """为用户签发 7 天有效的 HS256 bearer 访问令牌。"""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + ACCESS_TOKEN_TTL,
        "iss": JWT_ISSUER,
    }
    return jwt.encode(payload, get_settings().auth_jwt_secret, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str | None:
    """验证访问令牌并返回 subject 用户 ID；无效或过期时返回 None。"""
    try:
        payload = jwt.decode(
            token,
            get_settings().auth_jwt_secret,
            algorithms=[JWT_ALGORITHM],
            issuer=JWT_ISSUER,
            options={"require": ["sub", "iat", "exp", "iss"]},
        )
    except InvalidTokenError:
        return None

    subject = payload.get("sub")
    return subject if isinstance(subject, str) and subject else None
