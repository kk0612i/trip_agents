"""旅行业务数据仓库接口。

数据库访问由 TripService 的短工作单元进入，具体保存事务尚未实现。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.schemas.api_schema import ItineraryVersion, Page, PageQuery, TripView, VersionSummary
from app.core.errors import CapabilityUnavailableError
from app.models.trip import Trip
from app.schemas.trip_schema import Itinerary, RouteInfo, TripChangeRequest, TripRequest, ValidationResult


class TripRepository:
    """Trip 与 ItineraryVersion 的持久化入口。"""

    def __init__(self, session: AsyncSession) -> None:
        """保存外部工作单元注入的会话，不提交或关闭会话。

        Args:
            session: 当前数据操作使用的异步会话，不与并发任务共享。
        """
        # 数据访问依赖；生命周期由调用方管理。
        self.session = session

    async def load_current(self, trip_id: int) -> tuple[int, Itinerary] | None:
        """读取当前版本，在会话内把 JSON 转换成行程模型。

        Args:
            trip_id: 旅行主键。

        Returns:
            当前版本号与行程；旅行或当前版本不存在时返回 None。

        Raises:
            SQLAlchemyError: 读取失败时传播数据库异常。
            ValidationError: 已存储行程不符合内部 Schema。
        """
        stmt = (
            select(Trip)
            .options(joinedload(Trip.current_version))
            .where(Trip.id == trip_id)
        )

        trip = await self.session.scalar(stmt)

        if trip is None or trip.current_version is None:
            return None

        version = trip.current_version

        itinerary = Itinerary.model_validate(
            version.itinerary_json
        )

        return version.version_no, itinerary

    async def save_version(
        self,
        trip_id: int | None,
        trip_request: TripRequest | None,
        change_request: TripChangeRequest | None,
        itinerary: Itinerary,
        routes: list[RouteInfo],
        validation: ValidationResult,
    ) -> tuple[int, int]:
        """预留数据写入接口；不返回虚假的旅行编号或版本号。

        Args:
            trip_id: 旅行主键；新建时为 None。
            trip_request: 旅行需求快照。
            change_request: 修改要求；新建时可以为空。
            itinerary: 待保存的行程草稿。
            routes: 草稿对应的路线快照。
            validation: 当前草稿的校验结果。

        Returns:
            实现后的契约为旅行主键与版本号；当前不会返回。

        Raises:
            CapabilityUnavailableError: 版本写入尚未实现。
        """
        raise CapabilityUnavailableError("行程版本写入")

    async def get_trip(self, user_id: str, trip_id: str) -> TripView | None:
        """按可信用户身份读取旅行待实现；当前不执行查询。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("旅行归属读取")

    async def list_versions(self, user_id: str, trip_id: str, query: PageQuery) -> Page[VersionSummary]:
        """按用户和旅行范围分页读取版本待实现；不自行提交。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
            query: 已解析的页长与不透明游标；游标的数据语义尚待存储接入。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("行程版本列表读取")

    async def get_version(self, user_id: str, trip_id: str, version_no: int) -> ItineraryVersion | None:
        """按可信归属和业务版本号读取快照待实现。

        Args:
            user_id: 服务端验证后的用户标识，不能直接信任客户端或模型提供的值。
            trip_id: 旅行编号；公开入口使用正整数十进制字符串，内部读取使用整数。
            version_no: 从 1 开始的业务版本序号，不是版本表主键。

        Raises:
            CapabilityUnavailableError: 真实存储、鉴权或调度尚未接入；当前不产生成功结果。
        """
        raise CapabilityUnavailableError("行程版本详情读取")
