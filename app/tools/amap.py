"""高德能力的应用级工具。

工具只接收领域参数并调用 ``AmapService``；高德 API 参数、坐标和原始
响应均留在服务/客户端层，不能由 Agent 直接访问。
"""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any

from langchain.tools import ToolRuntime, tool

from app.schemas.agent_schema import ActionResult
from app.schemas.trip_schema import TripRequest
from app.tools.context import ToolContext
from app.tools.errors import RegistryError
from app.tools.execution import guarded_tool
from app.schemas.tool_schema import (
    CalculateRouteArguments,
    GetPlaceDetailArguments,
    GetWeatherForecastArguments,
    PlaceReference,
    SearchAccommodationArguments,
    SearchAttractionsArguments,
)


def _now() -> str:
    """生成带 UTC 时区的事实获取时间，供结果使用方判断时效。"""
    return datetime.now(timezone.utc).isoformat()


def _request(context: ToolContext) -> TripRequest:
    """从服务端提供的当前状态恢复旅行需求，缺失字段保持未知。"""
    return TripRequest.model_validate(context.state.get("trip_request") or {})


def _destination(context: ToolContext) -> str:
    """取得可信旅行目的地；尚未解析目的地时拒绝工具调用。"""
    destination = (_request(context).destination or "").strip()
    if not destination:
        raise RegistryError("工具需要当前旅行目的地")
    return destination


def _force_destination(context: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """用可信需求的目的地覆盖模型城市参数，返回独立参数副本。"""
    # 城市永远来自当前 TripRequest，模型传入的 city 只能被忽略，不能越权搜索其他城市。
    return {**arguments, "city": _destination(context)}


def _mapping(value: object) -> Mapping[str, Any]:
    """接纳服务返回的字典或模型；不支持的响应结构返回空映射。"""
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        return dumped if isinstance(dumped, Mapping) else {}
    return {}


def _text(value: object) -> str | None:
    """清理外部文本字段，空文本及非文本保持未知而不强制转换。"""
    if isinstance(value, str):
        return value.strip() or None
    return None


def _place(value: object) -> dict[str, Any]:
    """提取可公开的 POI 事实，坐标保留来源值，禁止补造价格。"""
    raw = _mapping(value)
    place_id = _text(raw.get("place_id"))
    name = _text(raw.get("name")) or ""
    category = _text(raw.get("category")) or "其他"
    return {
        "place_id": place_id,
        "name": name,
        "category": category,
        "address": _text(raw.get("address")),
        "city": _text(raw.get("city")),
        "longitude": raw.get("longitude"),
        "latitude": raw.get("latitude"),
        "source": "amap",
    }


def _places_result(values: list[object]) -> list[dict[str, Any]]:
    """按来源顺序保留有效且唯一的 POI，去除缺少编号或名称的记录。"""
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in values:
        place = _place(raw)
        if not place["place_id"] or not place["name"] or place["place_id"] in seen:
            continue
        seen.add(place["place_id"])
        result.append(place)
    return result


@tool(args_schema=SearchAttractionsArguments, response_format="content_and_artifact")
@guarded_tool("search_attractions", normalize=_force_destination)
async def search_attractions(
    keyword: str,
    city: str | None,
    limit: int,
    runtime: ToolRuntime[ToolContext],
) -> ActionResult:
    """在当前旅行目的地搜索真实 POI，不生成推荐或行程事实。"""
    service = runtime.context.amap
    if service is None:
        return ActionResult(status="unimplemented", message="未注入高德服务")
    city = _destination(runtime.context)
    places = _places_result(await service.search_places_by_query(keyword=keyword, city=city, limit=limit))
    runtime.context.remember_places(places)
    return ActionResult(data={
        "keyword": keyword,
        "city": city,
        "candidates": places,
        "source": "amap",
        "fetched_at": _now(),
        "warnings": [],
    })


@tool(args_schema=GetPlaceDetailArguments, response_format="content_and_artifact")
@guarded_tool("get_place_detail")
async def get_place_detail(place_id: str, runtime: ToolRuntime[ToolContext]) -> ActionResult:
    """查询本轮搜索得到的 POI 详情；未知字段保持 null。"""
    evidence = runtime.context.place_evidence.get(place_id)
    if evidence is None:
        return ActionResult(status="failed", message="POI 不在本轮搜索结果中")
    service = runtime.context.amap
    if service is None:
        return ActionResult(status="unimplemented", message="未注入高德服务")
    detail = await service.get_place_detail(place_id=place_id)
    data = _place(detail)
    data.update({
        "ticket_price": _mapping(detail).get("ticket_price"),
        "opening_hours": _mapping(detail).get("opening_hours"),
        "indoor": _mapping(detail).get("indoor"),
        "source": "amap",
        "fetched_at": _now(),
        "warnings": [],
    })
    return ActionResult(data=data)


def _safe_reference(ref: PlaceReference) -> dict[str, str | None]:
    """投影路线端点的可公开身份，避免向模型暴露坐标。"""
    return {"place_id": ref.place_id, "name": ref.name, "address": ref.address}


@tool(args_schema=CalculateRouteArguments, response_format="content_and_artifact")
@guarded_tool("calculate_route")
async def calculate_route(
    origin: PlaceReference,
    destination: PlaceReference,
    mode: str,
    runtime: ToolRuntime[ToolContext],
) -> ActionResult:
    """计算两个地点路线，向 Agent 隐藏坐标与高德原始响应。"""
    service = runtime.context.amap
    if service is None:
        return ActionResult(status="unimplemented", message="未注入高德服务")
    trip_city = (_request(runtime.context).destination or "").strip() or None
    origin = PlaceReference.model_validate(origin)
    destination = PlaceReference.model_validate(destination)
    # 坐标复用、缺坐标时的地理编码和高德响应转换均由领域服务负责。
    response = await service.calculate_route_between(
        origin=origin, destination=destination, mode=mode, city=trip_city)
    raw = _mapping(response)
    distance = raw.get("distance_km")
    duration = raw.get("duration_minutes")
    return ActionResult(data={
        "origin": _safe_reference(origin),
        "destination": _safe_reference(destination),
        "mode": mode,
        "distance_km": round(float(distance), 3) if distance is not None else None,
        "duration_minutes": round(float(duration)) if duration is not None else None,
        "provider": "amap",
        "fetched_at": _now(),
        "warnings": [],
    })


@tool(args_schema=SearchAccommodationArguments, response_format="content_and_artifact")
@guarded_tool("search_accommodation", normalize=_force_destination)
async def search_accommodation(
    keyword: str,
    city: str | None,
    limit: int,
    runtime: ToolRuntime[ToolContext],
) -> ActionResult:
    """搜索住宿 POI；高德结果不能证明房价、库存或可预订性。"""
    service = runtime.context.amap
    if service is None:
        return ActionResult(status="unimplemented", message="未注入高德服务")
    city = _destination(runtime.context)
    places = _places_result(await service.search_places_by_query(keyword=keyword, city=city, limit=limit))
    for place in places:
        place["price_status"] = "unknown"
    runtime.context.remember_places(places)
    return ActionResult(data={
        "keyword": keyword,
        "city": city,
        "candidates": places,
        "source": "amap",
        "fetched_at": _now(),
        "warnings": ["高德 POI 只能证明住宿地点存在，不能证明实时房价、库存或可预订性"],
    })


@tool(args_schema=GetWeatherForecastArguments, response_format="content_and_artifact")
@guarded_tool("get_weather_forecast", normalize=_force_destination)
async def get_weather_forecast(
    city: str | None,
    runtime: ToolRuntime[ToolContext],
) -> ActionResult:
    """查询当前旅行目的地天气事实，不直接修改行程。"""
    request = _request(runtime.context)
    if request.start_date is None:
        return ActionResult(status="unimplemented", message="缺少出发日期，暂不查询天气")
    service = runtime.context.amap
    if service is None:
        return ActionResult(status="unimplemented", message="未注入高德服务")
    city = _destination(runtime.context)
    from app.client.amap_client import AmapClientError
    from app.services.amap_service import AmapServiceError

    try:
        weather = await service.get_weather(city=city)
    except (AmapClientError, AmapServiceError):
        # 上游异常的 URL、响应文本不得进入模型消息或 trace。
        return ActionResult(status="unavailable", message="高德天气接口暂不可用")
    return ActionResult(data={
        "city": city,
        "weather": _mapping(weather),
        "source": "amap",
        "fetched_at": _now(),
        "warnings": [],
    })
