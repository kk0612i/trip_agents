"""会话业务：创建、列表分页、游标验证和公开摘要投影。"""
from copy import deepcopy
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import CapabilityUnavailableError, TripNotFoundError, TripHasNoVersionError
from app.core.log import log_event
from app.models import ChatSession
from app.repository.session_repository import SessionRepository
from app.schemas.api_schema import (Page, PageQuery, SessionCreate, SessionView, SessionSummary, TripRequestDTO)
from app.services.session_cursor import decode_session_cursor, encode_session_cursor


class SessionService:
    """使用请求级数据库会话组织对话业务，事务由服务管理。

    数据库会话由调用方关闭；服务及仓库不得跨并发任务共享。
    """

    def __init__(self, session: AsyncSession, *, cursor_secret: str) -> None:
        """保存借用会话，并创建使用同一会话的对话仓库。

        Args:
            session: 当前请求或工作单元的数据库异步会话，由调用方创建和关闭。
            cursor_secret: 装配层提供的持久签名密钥，仅用于游标编解码。
        """
        self.session = session
        self.session_repository = SessionRepository(session)
        # 密钥仅保存在服务依赖中，不进入响应或日志。
        self.cursor_secret = cursor_secret

    async def create_session(self, payload: SessionCreate, user_id: str) -> SessionView:
        """创建当前用户的会话，可从已有旅行当前版本复制需求。

        Args:
            payload: 已校验的可选旅行编号，不包含可信身份。
            user_id: 服务端认证依赖提供的用户编号。

        Returns:
            事务成功提交后的会话详情；新旅行相关字段保持 None。

        Raises:
            TripNotFoundError: 旅行不存在或不属于当前用户。
            TripHasNoVersionError: 旅行没有有效的当前正式版本。
        """
        # 单调时钟用于统计业务耗时，不参与持久化时间计算。
        started_at = monotonic()
        session_repo = self.session_repository
        async with self.session.begin():
            # 未关联旅行时不读取或创建旅行，也不启动 Agent。
            trip_id = int(payload.trip_id) if payload.trip_id is not None else None
            version_no = None
            request_snapshot = None
            trip_request = None
            if trip_id is not None:
                # 先验证归属，再读取正式版本，统一隐藏其他用户的资源。
                trip = await session_repo.get_owned_trip(user_id=user_id, trip_id=trip_id)
                if trip is None:
                    raise TripNotFoundError()
                # 仓库同时校验当前版本属于该旅行，拒绝错误的跨旅行指针。
                versions = await session_repo.get_current_versions(user_id=user_id, trip_ids=[trip_id])
                version = versions.get(trip_id)
                if version is None:
                    raise TripHasNoVersionError()
                version_no = version.version_no
                # 独立复制嵌套 JSON，避免后续会话修改污染正式版本快照。
                request_snapshot = deepcopy(version.request_snapshot_json)
                trip_request = TripRequestDTO.model_validate(request_snapshot)

            # MySQL DATETIME 存储无时区 UTC；公开响应恢复显式 UTC 时区。
            now = datetime.now(timezone.utc)
            chat_session = ChatSession(
                session_id=str(uuid4()), user_id=user_id, trip_id=trip_id,
                trip_request_json=request_snapshot, pending_question=None,
                latest_run_id=None, active_run_id=None,
                created_at=now.replace(tzinfo=None), updated_at=now.replace(tzinfo=None),
            )
            await session_repo.create_session(chat_session)
            # 提交前完成 DTO 校验，避免提交后读取过期 ORM 属性触发异步懒加载。
            view = SessionView(
                session_id=chat_session.session_id, trip_id=payload.trip_id,
                current_version_no=version_no, trip_request=trip_request,
                created_at=now, updated_at=now,
            )
        # 只有退出事务并提交成功才记录成功，不输出需求快照。
        log_event("session_created", status="succeeded", duration_ms=(monotonic() - started_at) * 1000)
        return view

    async def list_sessions(self, query: PageQuery, user_id: str) -> Page[SessionSummary]:
        """返回当前用户的一页会话摘要及后续游标。

        Args:
            query: 已完成基础校验的页长与原始游标。
            user_id: 来自服务端认证依赖的用户编号。

        Returns:
            会话摘要列表；末页或空列表的 next_cursor 为 None。

        Raises:
            InvalidCursorError: 游标格式、签名、用户或资源不合法。
        """
        # 解码发生在业务层，仓库只接收可直接参与 SQL 比较的位置。
        after = decode_session_cursor(
            query.cursor, user_id=user_id, secret=self.cursor_secret,
        ) if query.cursor is not None else None

        # 多查一条的逻辑由仓库负责，返回列表不包含探测用的额外记录。
        rows, has_more = await self.session_repository.list_sessions(
            user_id=user_id, limit=query.limit, after=after,
        )

        # 去重后批量读取正式版本，查询次数不随本页会话数增长。
        trip_ids = list({row.trip_id for row in rows if row.trip_id is not None})
        versions = await self.session_repository.get_current_versions(user_id=user_id, trip_ids=trip_ids)

        # 在请求数据库会话关闭前完成响应投影。
        items: list[SessionSummary] = []
        for row in rows:
            # 已绑定旅行只采用当前正式版本需求，未保存会话采用自身需求。
            version = versions.get(row.trip_id) if row.trip_id is not None else None
            if row.trip_id is None:
                request = row.trip_request_json or {}
            else:
                request = version.request_snapshot_json if version else {}
            # 标题只使用已保存字段，不额外调用模型生成。
            destination = request.get("destination")
            days = request.get("days")
            title = "新旅行"
            if isinstance(destination, str) and destination.strip():
                title = f"{destination.strip()}之旅"
                if type(days) is int and days > 0:
                    title = f"{destination.strip()} {days} 日游"
            items.append(SessionSummary(
                session_id=row.session_id,
                title=title,
                trip_id=str(row.trip_id) if row.trip_id is not None else None,
                current_version_no=version.version_no if version else None,
                # MySQL 返回无时区的 UTC DATETIME，公开 DTO 要求显式时区。
                created_at=row.created_at.replace(tzinfo=timezone.utc),
                updated_at=row.updated_at.replace(tzinfo=timezone.utc),
            ))

        # 使用实际返回的最后一条生成下一页位置，不能用多查的那一条。
        next_cursor = None
        if has_more:
            last = rows[-1]
            next_cursor = encode_session_cursor(
                user_id=user_id, created_at=last.created_at,
                session_id=last.session_id, secret=self.cursor_secret,
            )
        return Page[SessionSummary](items=items, next_cursor=next_cursor)

    async def get_session(
            self,
            session_id: str,
            user_id: str,
    ) -> SessionView:
        """会话读取待实现；接入前必须验证身份和资源归属。

        Args:
            session_id: 已由入口校验的请求参数；不代表已获访问授权。
            user_id: 当前登录的用户id
        Raises:
            CapabilityUnavailableError: 会话读取尚未接入。
        """
        raise CapabilityUnavailableError("会话读取")
