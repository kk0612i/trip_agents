"""会话列表游标：签名保护分页位置，并绑定用户与查询资源。"""

from datetime import datetime, timezone
from typing import Literal

import jwt
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError

from app.core.errors import InvalidCursorError
from app.schemas.api_schema import APIModel, UTCTimestamp, UUIDString

# 独立用途标识，防止分页游标与登录令牌或其他列表游标混用。
CURSOR_ISSUER = "trip-agents:pagination"
CURSOR_RESOURCE = "sessions"


class _SessionCursor(APIModel):
    """签名载荷；客户端只需原样传回整个字符串。"""

    # 游标格式版本，用于拒绝不兼容的旧格式。
    version: Literal["1"] = "1"
    # 签发来源及所属查询资源。
    iss: Literal["trip-agents:pagination"] = CURSOR_ISSUER
    aud: Literal["sessions"] = CURSOR_RESOURCE
    # 当前登录用户及上一页最后一条记录的位置。
    user_id: UUIDString
    created_at: UTCTimestamp
    session_id: UUIDString


def encode_session_cursor(
    *, user_id: str, created_at: datetime, session_id: str, secret: str,
) -> str:
    """为已返回的最后一条会话生成签名游标。

    Args:
        user_id: 服务端认证的用户编号。
        created_at: 数据库 UTC 创建时间，无时区时显式按 UTC 解释。
        session_id: 本页最后一条会话编号。
        secret: 装配层注入的持久签名密钥。

    Returns:
        客户端应原样传回的游标字符串；签名不代表加密。
    """
    # MySQL DATETIME 不携带时区，编码时保留完整微秒精度。
    timestamp = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at
    payload = _SessionCursor(user_id=user_id, created_at=timestamp, session_id=session_id)
    return jwt.encode(payload.model_dump(mode="json"), secret, algorithm="HS256")


def decode_session_cursor(
    cursor: str, *, user_id: str, secret: str,
) -> tuple[datetime, str]:
    """校验签名、资源、用户和位置，返回供数据库查询的游标。

    Args:
        cursor: 客户端传回的原始游标。
        user_id: 当前登录用户编号，不能从游标中取得可信身份。
        secret: 装配层注入的持久签名密钥。

    Returns:
        UTC 无时区创建时间与会话编号，匹配 MySQL DATETIME 存储方式。

    Raises:
        InvalidCursorError: 签名、格式、用户或查询资源不匹配。
    """
    if len(cursor) > 2048:
        raise InvalidCursorError()
    try:
        # 固定算法和用途；必填声明防止其他令牌被当作分页游标。
        decoded = jwt.decode(
            cursor, secret, algorithms=["HS256"],
            issuer=CURSOR_ISSUER, audience=CURSOR_RESOURCE,
            options={"require": ["version", "iss", "aud", "user_id", "created_at", "session_id"]},
        )
        payload = _SessionCursor.model_validate(decoded)
    except (InvalidTokenError, ValidationError) as exc:
        raise InvalidCursorError() from exc
    if payload.user_id != user_id or payload.created_at.year < 1000:
        raise InvalidCursorError()
    return payload.created_at.replace(tzinfo=None), payload.session_id
