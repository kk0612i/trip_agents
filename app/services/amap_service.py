"""高德地图能力的应用服务。

客户端只负责发送请求；本模块负责将旅行需求转换为搜索任务、将高德字段转换
为领域模型，以及把每天相邻的行程项拆成路线分段。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Iterable, Mapping
from typing import Any, Literal

from app.client.amap_client import AmapClient
from app.models.schemas import (
    Itinerary,
    ItineraryItem,
    PlaceCandidate,
    RouteInfo,
    TripChangeRequest,
    TripRequest,
)


class AmapServiceError(RuntimeError):
    """高德响应缺少业务所需数据，无法继续生成行程。"""


class AmapService:
    """封装 POI 搜索、候选地点转换和相邻地点路线计算。"""

    def __init__(
        self,
        amap_client: AmapClient,
        *,
        search_limit_per_keyword: int = 10,
        max_candidates: int = 30,
        route_mode: Literal["walking", "driving"] = "walking",
    ) -> None:
        """

        :param amap_client: 实际调用高德接口的客户端
        :param search_limit_per_keyword: 每个搜索词最多返回多少条
        :param max_candidates: 最终最多返回多少个候选地点
        :param route_mode: 路线方式，目前支持 walking 和 driving
        """
        if not 1 <= search_limit_per_keyword <= 25:
            raise ValueError("search_limit_per_keyword 必须在 1 到 25 之间")
        if max_candidates <= 0:
            raise ValueError("max_candidates 必须大于 0")
        if route_mode not in {"walking", "driving"}:
            raise ValueError(f"暂不支持的路线方式: {route_mode}")

        self.amap_client = amap_client
        self.search_limit_per_keyword = search_limit_per_keyword
        self.max_candidates = max_candidates
        self.route_mode = route_mode

    async def search_places(
        self,
        request: TripRequest | TripChangeRequest,
    ) -> list[PlaceCandidate]:
        """根据创建或修改需求搜索地点，并转换为去重后的候选地点。

        创建行程时使用目的地限制搜索城市，并始终搜索旅游景点；用户偏好会
        作为补充关键词。修改行程时只搜索明确要求新增的地点。
        """

        search_tasks = self._build_search_tasks(request)
        if not search_tasks:
            return []

        # 并发提交搜索任务，实际发出频率由 AmapClient 的每秒 3 次限流器控制。
        responses = await asyncio.gather(
            *(
                self.amap_client.search_pois(
                    keywords=keyword,
                    city=city,
                    offset=self.search_limit_per_keyword,
                )
                for keyword, city in search_tasks
            )
        )

        candidates: list[PlaceCandidate] = []
        seen_place_ids: set[str] = set()
        for response in responses:
            pois = response.get("pois", [])
            if not isinstance(pois, list):
                raise AmapServiceError("高德地点搜索响应中的 pois 格式不正确")

            for poi in pois:
                candidate = self._to_place_candidate(poi)
                if candidate is None or candidate.place_id in seen_place_ids:
                    continue

                seen_place_ids.add(candidate.place_id)
                candidates.append(candidate)
                if len(candidates) >= self.max_candidates:
                    return candidates

        return candidates

    async def calculate_route(self, itinerary: Itinerary) -> list[RouteInfo]:
        """计算每天相邻行程项之间的路线。

        ``ItineraryItem`` 当前不保存经纬度，因此先按地址和地点名做地理编码。
        同一地点在整份行程中只编码一次，然后再并发查询各路线分段。
        """

        legs = [
            (previous, current)
            for day in itinerary.days
            for previous, current in zip(day.items, day.items[1:])
        ]
        if not legs:
            return []

        items = [item for leg in legs for item in leg]
        locations = await self._resolve_locations(items)

        route_responses = await asyncio.gather(
            *(
                self.amap_client.calculate_route(
                    origin=locations[self._location_query(previous)],
                    destination=locations[self._location_query(current)],
                    mode=self.route_mode,
                )
                for previous, current in legs
            )
        )

        return [
            self._to_route_info(previous, current, response)
            for (previous, current), response in zip(legs, route_responses)
        ]

    @staticmethod
    def _build_search_tasks(
        request: TripRequest | TripChangeRequest,
    ) -> list[tuple[str, str | None]]:
        """把领域请求转换为有序且不重复的“关键词、城市”任务。
        例如：
        创建行程:
        [
            ("旅游景点", "长沙"),
            ("美食", "长沙"),
            ("历史", "长沙"),
        ]

        修改行程:
        [("岳麓山", None)]

        输出: 去重后的候选地点
        """

        if isinstance(request, TripRequest):
            destination = (request.destination or "").strip()
            if not destination:
                raise ValueError("搜索候选地点前必须提供旅行目的地")
            keywords = ["旅游景点", *request.preferences]
            city: str | None = destination
        elif isinstance(request, TripChangeRequest):
            keywords = request.add_place_keywords
            city = None
        else:
            raise TypeError(f"不支持的请求类型: {type(request).__name__}")

        tasks: list[tuple[str, str | None]] = []
        seen_keywords: set[str] = set()
        for raw_keyword in keywords:
            keyword = raw_keyword.strip()
            if not keyword or keyword in seen_keywords:
                continue
            seen_keywords.add(keyword)
            tasks.append((keyword, city))
        return tasks

    @classmethod
    def _to_place_candidate(
        cls,
        raw_poi: object,
    ) -> PlaceCandidate | None:
        """将一条高德 POI 转换为 PlaceCandidate(系统候选地点)；无名称或坐标时忽略。"""

        if not isinstance(raw_poi, Mapping):
            return None

        name = cls._as_text(raw_poi.get("name"))
        location = cls._parse_location(raw_poi.get("location"))
        if not name or location is None:
            return None

        longitude, latitude = location
        amap_poi_id = cls._as_text(raw_poi.get("id")) or None
        # 极少数响应可能没有 POI ID，用名称和坐标组成当前结果内的稳定标识。
        place_id = amap_poi_id or f"amap:{name}:{longitude},{latitude}"

        raw_types = cls._as_text(raw_poi.get("type"))
        type_parts = cls._unique_texts(raw_types.split(";"))
        category = type_parts[-1] if type_parts else "其他"

        return PlaceCandidate(
            place_id=place_id,
            name=name,
            category=category,
            address=cls._as_text(raw_poi.get("address")) or None,
            amap_poi_id=amap_poi_id,
            latitude=latitude,
            longitude=longitude,
            # 高德基础 POI 不提供可靠游玩时长和门票价格，使用明确的规划默认值。
            recommended_duration_minutes=cls._recommended_duration(raw_types),
            estimated_cost=0,
            tags=type_parts,
            source_url=None,
        )

    async def _resolve_locations(
        self,
        items: Iterable[ItineraryItem],
    ) -> dict[str, str]:
        """为路线涉及的地点查询坐标，并复用重复地点的结果。"""

        items_by_query: dict[str, ItineraryItem] = {}
        for item in items:
            items_by_query.setdefault(self._location_query(item), item)

        queries = list(items_by_query)
        responses = await asyncio.gather(
            *(self.amap_client.geocode(address=query) for query in queries)
        )

        locations: dict[str, str] = {}
        for query, response in zip(queries, responses):
            geocodes = response.get("geocodes", [])
            if not isinstance(geocodes, list) or not geocodes:
                item = items_by_query[query]
                raise AmapServiceError(f"未找到地点“{item.name}”的坐标")

            first = geocodes[0]
            raw_location = first.get("location") if isinstance(first, Mapping) else None
            parsed_location = self._parse_location(raw_location)
            if parsed_location is None:
                item = items_by_query[query]
                raise AmapServiceError(f"地点“{item.name}”的坐标格式不正确")

            longitude, latitude = parsed_location
            locations[query] = f"{longitude},{latitude}"

        return locations

    def _to_route_info(
        self,
        previous: ItineraryItem,
        current: ItineraryItem,
        response: Mapping[str, Any],
    ) -> RouteInfo:
        """从高德路线响应的首选路径构造领域路线对象。"""

        route = response.get("route")
        paths = route.get("paths") if isinstance(route, Mapping) else None
        if not isinstance(paths, list) or not paths:
            raise AmapServiceError(f"未找到“{previous.name}”到“{current.name}”的路线")

        first_path = paths[0]
        if not isinstance(first_path, Mapping):
            raise AmapServiceError("高德路线响应中的 paths 格式不正确")

        try:
            distance_meters = float(first_path["distance"])
            duration_seconds = float(first_path["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AmapServiceError("高德路线响应缺少有效的距离或时长") from exc

        if distance_meters < 0 or duration_seconds < 0:
            raise AmapServiceError("高德路线响应中的距离或时长不能为负数")

        return RouteInfo(
            from_item_id=previous.item_id,
            to_item_id=current.item_id,
            distance_km=round(distance_meters / 1000, 3),
            # 不足一分钟按一分钟计，避免向用户展示 0 分钟的非零路线。
            duration_minutes=(
                math.ceil(duration_seconds / 60) if duration_seconds > 0 else 0
            ),
            mode=self.route_mode,
            provider="amap",
        )

    @staticmethod
    def _location_query(item: ItineraryItem) -> str:
        """组合地址和名称，提高同名地点的地理编码准确度。"""

        parts = [part.strip() for part in (item.address, item.name) if part]
        return " ".join(dict.fromkeys(parts))

    @staticmethod
    def _parse_location(value: object) -> tuple[float, float] | None:
        """解析并校验高德的“经度,纬度”坐标。"""

        if not isinstance(value, str):
            return None
        parts = value.split(",")
        if len(parts) != 2:
            return None
        try:
            longitude, latitude = (float(part) for part in parts)
        except ValueError:
            return None
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            return None
        return longitude, latitude

    @staticmethod
    def _as_text(value: object) -> str:
        """兼容高德用空数组表示空文本的响应形式。"""

        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return "".join(str(part) for part in value).strip()
        return ""

    @staticmethod
    def _unique_texts(values: Iterable[str]) -> list[str]:
        """按原顺序清理并去重文本。"""

        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @staticmethod
    def _recommended_duration(raw_types: str) -> int:
        """按 POI 大类给出保守的行程占用时长。"""

        if "餐饮" in raw_types:
            return 90
        if any(keyword in raw_types for keyword in ("博物馆", "展览馆", "风景名胜")):
            return 180
        return 120
