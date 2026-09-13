"""旅行业务数据仓库接口。

数据库访问只允许从 load_context 和 save_version 节点进入，具体 ORM 事务以后实现。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio.session import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Trip, ItineraryVersion
from app.models.schemas import (
    Itinerary,
    RouteInfo,
    TripChangeRequest,
    TripRequest,
    ValidationResult,
)


class TripRepository:
    """Trip 与 ItineraryVersion 的持久化入口。"""
    def __init__(self, session: AsyncSession):
        self.session = session
    async def load_current(self, trip_id: int) -> tuple[int, Itinerary] | None:
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
        pass
