"""高德地图能力的应用层接口。"""

from __future__ import annotations

from app.models.schemas import (
    Itinerary,
    PlaceCandidate,
    RouteInfo,
    TripChangeRequest,
    TripRequest,
)


class AmapService:
    """封装 POI 搜索和路线计算；具体 HTTP 调用以后在这里实现。"""

    def __init__(self, amap_client):
        self.amap_client = amap_client

    async def search_places(
        self,
        request: TripRequest | TripChangeRequest,
    ) -> list[PlaceCandidate]:
        pass

    async def calculate_route(self, itinerary: Itinerary) -> list[RouteInfo]:
        pass
