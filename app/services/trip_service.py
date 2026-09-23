"""旅行加载与保存边界；每次数据操作独立管理短会话。"""

from app.core.db import SessionFactory
from time import perf_counter
from app.core.log import log_event

from app.schemas.api_schema import ItineraryVersion, Page, PageQuery, TripView, VersionSummary
from app.core.errors import CapabilityUnavailableError
from app.repository.trip_repository import TripRepository
from app.schemas.trip_schema import (
    Itinerary,
    RouteInfo,
    TripChangeRequest,
    TripRequest,
    ValidationResult,
)

class TripService:
    """提供旅行快照读取与待实现的版本保存入口。

    仅接收外部注入的会话工厂，不持有请求级 Session。Runtime
    调用本服务，权限、预算及草稿校验证明仍由 Agent 执行层管理。
    """

    def __init__(self, session_factory: SessionFactory) -> None:
        """保存创建短会话的工厂，不连接数据库。

        Args:
            session_factory: 每次调用创建独立会话的异步上下文工厂。
        """
        # 每个业务工作单元单独借出、关闭会话；服务可以跨运行复用。
        self.session_factory = session_factory

    async def load_current(self, trip_id: int) -> tuple[int, Itinerary] | None:
        """读取当前正式版本，并在退出会话前转换成内部行程模型。

        Args:
            trip_id: 调用方确认访问范围后的旅行编号。本方法尚不提供 HTTP 鉴权。

        Returns:
            当前版本号与独立行程快照；旅行不存在或没有当前版本时返回 None。

        Raises:
            SQLAlchemyError: 数据库读取失败，原异常向调用方传播。
            ValidationError: 已存储的行程 JSON 不符合内部 Schema。
        """
        started_at = perf_counter()
        async with self.session_factory() as session:
            # Repository 仅在本次读取中复用会话，不随模型推理保持存活。
            trip_repo = TripRepository(session)
            current = await trip_repo.load_current(trip_id)
        log_event("trip_load_completed", operation="load_current", status="completed" if current else "not_found",
                  duration_ms=round((perf_counter() - started_at) * 1000, 2))
        return current

    async def save_version(
        self,
        trip_id: int | None,
        trip_request: TripRequest | None,
        change_request: TripChangeRequest | None,
        itinerary: Itinerary,
        routes: list[RouteInfo],
        validation: ValidationResult,
    ) -> tuple[int, int]:
        """声明版本保存接口；本阶段不创建会话、不执行写入。

        Args:
            trip_id: 已有旅行编号；新旅行时为 None。
            trip_request: 当前旅行需求，缺失时为 None。
            change_request: 修改已有行程的要求；新建或未提供时为 None。
            itinerary: 已由执行层完成当前轮次校验的行程草稿。
            routes: 与当前草稿对应的路线证据。
            validation: 当前草稿的确定性校验结果。

        Returns:
            实现后的契约为旅行编号与版本号；当前不会返回成功结果。

        Raises:
            CapabilityUnavailableError: 版本保存事务、归属校验及幂等写入尚未实现。
        """
        log_event("trip_save_rejected", level="WARNING", operation="save_version", status="rejected",
                  reason_code="CAPABILITY_UNAVAILABLE")
        raise CapabilityUnavailableError("行程版本保存")

    async def get_trip(self, trip_id: str) -> TripView:
        """公开旅行读取待实现；接入前必须完成身份和归属验证。

        Args:
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("旅行读取")

    async def list_versions(self, trip_id: str, query: PageQuery) -> Page[VersionSummary]:
        """公开版本列表待实现；当前不创建会话或读取真实数据。

        Args:
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
            query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("行程版本列表")

    async def get_version(self, trip_id: str, version_no: int) -> ItineraryVersion:
        """公开版本详情待实现；版本号为业务序号，不是数据库主键。

        Args:
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
            version_no: 从 1 开始的业务版本序号，不是版本表主键。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("行程版本读取")
