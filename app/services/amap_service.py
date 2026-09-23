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
from app.schemas.trip_schema import Itinerary, ItineraryItem, PlaceCandidate, RouteInfo, TripChangeRequest, TripRequest
from app.schemas.place_search_schema import SearchPlaceCandidate


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
        """注入高德客户端与查询限制，不接管客户端资源所有权。

        Args:
            amap_client: 实际调用高德的客户端，由应用或调用方负责关闭。
            search_limit_per_keyword: 每个搜索词最多返回的候选数，范围为 1 到 25。
            max_candidates: 整份旅行需求最多保留的候选数。
            route_mode: 相邻行程项之间使用的路线方式。

        Raises:
            ValueError: 候选数量或路线方式不符合支持范围。
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

        Args:
            request: 创建需求或修改需求，创建需求必须含目的地。

        Returns:
            按 POI 编号去重的规划候选；未知价格和游玩时长保持 None。
        """

        search_tasks = self._build_search_tasks(request)
        if not search_tasks:
            return []

        # 并发提交搜索任务，实际发出频率由 AmapClient 的每秒 3 次限流器控制。
        search_results = await asyncio.gather(
            *(
                self.search_places_by_query(
                    keyword=keyword,
                    city=city,
                    limit=self.search_limit_per_keyword,
                )
                for keyword, city in search_tasks
            )
        )

        candidates: list[PlaceCandidate] = []
        seen_place_ids: set[str] = set()
        for facts in search_results:
            for fact in facts:
                if fact.place_id in seen_place_ids:
                    continue
                # 搜索事实显式转换为规划候选；不在适配阶段猜测票价或游玩时长。
                candidate = fact.to_planning_candidate()
                seen_place_ids.add(candidate.place_id)
                candidates.append(candidate)
                if len(candidates) >= self.max_candidates:
                    return candidates

        return candidates

    async def search_places_by_query(
        self,
        *,
        keyword: str,
        city: str | None = None,
        limit: int | None = None,
    ) -> list[SearchPlaceCandidate]:
        """按一个关键词搜索 POI，并转换为去重后的事实候选。

        这是应用工具使用的单次搜索入口；``search_places`` 负责把完整的
        ``TripRequest`` 拆成多个关键词后调用同一套转换规则。

        Args:
            keyword: 非空关键词。
            city: 可选城市范围，明确属于其他城市的候选被过滤。
            limit: 单次最多返回数量，None 使用服务默认值。

        Returns:
            只包含上游已提供事实的去重候选，不推断票价或营业时间。
        """

        keyword = keyword.strip()
        if not keyword:
            raise ValueError("搜索关键词不能为空")
        normalized_city = city.strip() if city and city.strip() else None
        requested_limit = self.search_limit_per_keyword if limit is None else limit
        if not 1 <= requested_limit <= 25:
            raise ValueError("limit 必须在 1 到 25 之间")

        response = await self.amap_client.search_pois(
            keywords=keyword,
            city=normalized_city,
            offset=requested_limit,
        )
        pois = response.get("pois", [])
        if not isinstance(pois, list):
            raise AmapServiceError("高德地点搜索响应中的 pois 格式不正确")

        candidates: list[SearchPlaceCandidate] = []
        seen_place_ids: set[str] = set()
        for poi in pois:
            candidate = self._to_search_candidate(poi, city=normalized_city)
            if candidate is None or candidate.place_id in seen_place_ids:
                continue
            seen_place_ids.add(candidate.place_id)
            candidates.append(candidate)
            if len(candidates) >= requested_limit:
                break
        return candidates

    async def get_place_detail(self, *, place_id: str) -> dict[str, Any]:
        """将高德 POI 详情转换为应用事实，不补充接口未返回的字段。

        Args:
            place_id: 本次需要查询的高德 POI 编号。

        Returns:
            已核对编号的详情；门票、营业时间及室内属性未知时为 None。
        """

        poi_id = place_id.strip()
        if not poi_id:
            raise ValueError("POI ID 不能为空")
        response = await self.amap_client.get_poi_detail(poi_id=poi_id)
        pois = response.get("pois", [])
        if not isinstance(pois, list) or not pois:
            raise AmapServiceError(f"未找到 POI “{poi_id}”的详情")
        first = pois[0]
        if not isinstance(first, Mapping):
            raise AmapServiceError("高德 POI 详情响应格式不正确")
        if self._as_text(first.get("id")) != poi_id:
            raise AmapServiceError("高德 POI 详情与请求的地点不一致")
        return self._to_place_detail(first, fallback_id=poi_id)

    async def get_weather(self, *, city: str) -> dict[str, Any]:
        """将高德天气响应转换为稳定的事实结构。

        Args:
            city: 非空城市名或行政区代码。

        Returns:
            规范化预报列表；没有任何可用预报时抛出 AmapServiceError。
        """

        normalized_city = city.strip()
        if not normalized_city:
            raise ValueError("天气查询城市不能为空")
        response = await self.amap_client.get_weather(city=normalized_city)
        forecasts = response.get("forecasts", [])
        if not isinstance(forecasts, list):
            raise AmapServiceError("高德天气响应中的 forecasts 格式不正确")

        normalized_forecasts: list[dict[str, Any]] = []
        for forecast in forecasts:
            if not isinstance(forecast, Mapping):
                continue
            casts = forecast.get("casts", [])
            if not isinstance(casts, list):
                casts = []
            normalized_forecasts.append(
                {
                    "city": self._as_text(forecast.get("city")) or normalized_city,
                    "adcode": self._as_text(forecast.get("adcode")) or None,
                    "province": self._as_text(forecast.get("province")) or None,
                    "report_time": self._as_text(forecast.get("reporttime")) or None,
                    "casts": [
                        self._to_weather_cast(cast)
                        for cast in casts
                        if isinstance(cast, Mapping)
                    ],
                }
            )
        if not any(forecast["casts"] for forecast in normalized_forecasts):
            raise AmapServiceError("高德天气接口没有返回可用预报")
        return {
            "city": normalized_city,
            "forecasts": normalized_forecasts,
            "source": "amap",
        }

    async def calculate_route_between(
        self,
        *,
        origin: object,
        destination: object,
        mode: Literal["walking", "driving"] = "walking",
        city: str | None = None,
    ) -> dict[str, Any]:
        """计算两个应用地点之间的路线，并隐藏高德坐标和原始响应格式。

        地点引用若已携带完整坐标则直接规划；否则仅在服务内部进行一次地理编码。

        Args:
            origin: 起点引用，支持领域对象或字典。
            destination: 终点引用，支持领域对象或字典。
            mode: 步行或驾车方式。
            city: 缺少坐标时用于地理编码的可选城市范围。

        Returns:
            应用路线事实；距离单位为千米，时长单位为分钟。
        """

        if mode not in {"walking", "driving"}:
            raise ValueError(f"暂不支持的路线方式: {mode}")
        origin_coordinates = await self._coordinates_for_reference(origin, city=city)
        destination_coordinates = await self._coordinates_for_reference(destination, city=city)
        response = await self.amap_client.calculate_route(
            origin=origin_coordinates,
            destination=destination_coordinates,
            mode=mode,
        )
        distance_km, duration_minutes = self._route_metrics(response)
        return {
            "origin": self._reference_label(origin),
            "destination": self._reference_label(destination),
            "mode": mode,
            "distance_km": distance_km,
            "duration_minutes": duration_minutes,
            "provider": "amap",
            "warnings": [],
        }

    async def _coordinates_for_reference(self, reference: object, *, city: str | None = None) -> str:
        """从应用地点引用读取坐标；缺失时由服务内部地理编码。"""

        longitude = self._reference_value(reference, "longitude")
        latitude = self._reference_value(reference, "latitude")
        if longitude is not None and latitude is not None:
            parsed = self._parse_location(f"{longitude},{latitude}")
            if parsed is not None:
                return f"{parsed[0]},{parsed[1]}"

        address = self._reference_value(reference, "address")
        name = self._reference_value(reference, "name")
        query = " ".join(dict.fromkeys(part.strip() for part in (address, name) if part))
        if not query:
            raise AmapServiceError("路线地点缺少名称或地址")
        response = await self.amap_client.geocode(address=query, city=city)
        geocodes = response.get("geocodes", [])
        if not isinstance(geocodes, list) or not geocodes:
            raise AmapServiceError(f"未找到地点“{name or query}”的坐标")
        first = geocodes[0]
        location = first.get("location") if isinstance(first, Mapping) else None
        parsed = self._parse_location(location)
        if parsed is None:
            raise AmapServiceError(f"地点“{name or query}”的坐标格式不正确")
        return f"{parsed[0]},{parsed[1]}"

    @staticmethod
    def _reference_value(reference: object, field: str) -> Any:
        if isinstance(reference, Mapping):
            value = reference.get(field)
        else:
            value = getattr(reference, field, None)
        if isinstance(value, str):
            return value.strip() or None
        return value

    @classmethod
    def _reference_label(cls, reference: object) -> dict[str, Any]:
        return {
            "place_id": cls._reference_value(reference, "place_id"),
            "name": cls._reference_value(reference, "name"),
            "address": cls._reference_value(reference, "address"),
        }

    @classmethod
    def _route_metrics(cls, response: Mapping[str, Any]) -> tuple[float, int]:
        route = response.get("route")
        paths = route.get("paths") if isinstance(route, Mapping) else None
        if not isinstance(paths, list) or not paths:
            raise AmapServiceError("高德路线响应中没有可用路径")
        first_path = paths[0]
        if not isinstance(first_path, Mapping):
            raise AmapServiceError("高德路线响应中的 paths 格式不正确")
        try:
            distance_meters = float(first_path["distance"])
            duration_seconds = float(first_path["duration"])
        except (KeyError, TypeError, ValueError) as exc:
            raise AmapServiceError("高德路线响应缺少有效的距离或时长") from exc
        if not math.isfinite(distance_meters) or not math.isfinite(duration_seconds):
            raise AmapServiceError("高德路线响应中的距离或时长必须是有限数字")
        if distance_meters < 0 or duration_seconds < 0:
            raise AmapServiceError("高德路线响应中的距离或时长不能为负数")
        return round(distance_meters / 1000, 3), (
            math.ceil(duration_seconds / 60) if duration_seconds > 0 else 0
        )

    @classmethod
    def _to_place_detail(
        cls,
        raw_poi: Mapping[str, Any],
        *,
        fallback_id: str,
    ) -> dict[str, Any]:
        """详情字段只来自高德；未知价格、营业时间和室内属性保持 None。"""

        location = cls._parse_location(raw_poi.get("location"))
        longitude, latitude = location if location is not None else (None, None)
        raw_types = cls._as_text(raw_poi.get("type"))
        type_parts = cls._unique_texts(raw_types.split(";"))
        return {
            "place_id": cls._as_text(raw_poi.get("id")) or fallback_id,
            "name": cls._as_text(raw_poi.get("name")) or None,
            "category": type_parts[-1] if type_parts else None,
            "address": cls._as_text(raw_poi.get("address")) or None,
            "city": cls._as_text(raw_poi.get("cityname")) or None,
            "longitude": longitude,
            "latitude": latitude,
            "ticket_price": None,
            "opening_hours": None,
            "indoor": None,
            "source": "amap",
        }

    @classmethod
    def _to_weather_cast(cls, cast: Mapping[str, Any]) -> dict[str, Any]:
        """保留天气事实字段，并将高德空数组规范化为 None。"""

        return {
            "date": cls._as_text(cast.get("date")) or None,
            "week": cls._as_text(cast.get("week")) or None,
            "dayweather": cls._as_text(cast.get("dayweather")) or None,
            "nightweather": cls._as_text(cast.get("nightweather")) or None,
            "daytemp": cls._as_text(cast.get("daytemp")) or None,
            "nighttemp": cls._as_text(cast.get("nighttemp")) or None,
            "daywind": cls._as_text(cast.get("daywind")) or None,
            "nightwind": cls._as_text(cast.get("nightwind")) or None,
            "daypower": cls._as_text(cast.get("daypower")) or None,
            "nightpower": cls._as_text(cast.get("nightpower")) or None,
        }

    async def calculate_route(self, itinerary: Itinerary) -> list[RouteInfo]:
        """计算每天相邻行程项之间的路线。

        ``ItineraryItem`` 当前不保存经纬度，因此先按地址和地点名做地理编码。
        同一地点在整份行程中只编码一次，然后再并发查询各路线分段。

        Args:
            itinerary: 完整行程草稿，不跨天连接路线分段。

        Returns:
            按行程顺序返回路线；不足一分钟的非零时长向上取整为一分钟。
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
    def _to_search_candidate(
        cls,
        raw_poi: object,
        *,
        city: str | None = None,
    ) -> SearchPlaceCandidate | None:
        """转换 POI 搜索事实；缺少高德 ID 或名称时忽略，不虚构 POI 标识。"""

        if not isinstance(raw_poi, Mapping):
            return None

        name = cls._as_text(raw_poi.get("name"))
        location = cls._parse_location(raw_poi.get("location"))
        amap_poi_id = cls._as_text(raw_poi.get("id")) or None
        if not name or not amap_poi_id:
            return None
        longitude, latitude = location if location else (None, None)
        actual_city = cls._as_text(raw_poi.get("cityname")) or None
        if city and actual_city and not cls._city_matches(city, actual_city, raw_poi):
            return None

        raw_types = cls._as_text(raw_poi.get("type"))
        type_parts = cls._unique_texts(raw_types.split(";"))
        category = type_parts[-1] if type_parts else "其他"

        return SearchPlaceCandidate(
            place_id=amap_poi_id,
            name=name,
            category=category,
            address=cls._as_text(raw_poi.get("address")) or None,
            latitude=latitude,
            longitude=longitude,
            # POI 搜索事实不包含可靠游玩时长或门票价格，未知值必须保留为 None。
            city=actual_city,
            source="amap",
        )

    @classmethod
    def _city_matches(cls, expected: str, actual: str, poi: Mapping[str, Any]) -> bool:
        """兼容城市名及高德 citycode/adcode，拒绝明确属于其他城市的 POI。"""
        if expected.isdigit():
            return expected in {cls._as_text(poi.get("citycode")), cls._as_text(poi.get("adcode"))}
        return expected.removesuffix("市") == actual.removesuffix("市")

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

        distance_km, duration_minutes = self._route_metrics(response)

        return RouteInfo(
            from_item_id=previous.item_id,
            to_item_id=current.item_id,
            distance_km=distance_km,
            # 不足一分钟按一分钟计，避免向用户展示 0 分钟的非零路线。
            duration_minutes=duration_minutes,
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
