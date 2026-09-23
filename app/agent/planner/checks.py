"""行程地点、路线与费用证据验收，不发起工具请求或保存操作。"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from app.agent.planner.errors import PlanningError
from app.schemas.agent_schema import ActionResult
from app.schemas.place_search_schema import PlaceSearchResult
from app.schemas.planner_schema import _RouteSelection
from app.schemas.trip_schema import Itinerary, PlaceCandidate, RouteInfo, TripRequest

if TYPE_CHECKING:
    from app.agent.supervisor.supervisor_state import AgentRunState


def _cost_signature(
    items: Iterable[Mapping[str, Any]],
) -> Counter[tuple[str, float | None]]:
    """按地点及价格比较费用明细，保留重复项目的次数。"""
    return Counter((item["description"], item.get("amount")) for item in items)


def collect_places(
    state: AgentRunState, current: Itinerary | None,
) -> tuple[dict[str, PlaceCandidate], dict[str, Any]]:
    """收集本轮可采用的地点，修改时允许复用已有地点。

    Args:
        state: 当前主管状态，搜索结果可为模型对象或原始字典。
        current: 已有行程；新建时为空。

    Returns:
        按地点编号索引的证据及序列化搜索结果。

    Raises:
        PlanningError: 已提供的搜索结果尚未成功。
    """
    places: dict[str, PlaceCandidate] = {}
    if current:
        for day in current.days:
            for item in day.items:
                places[item.place_id] = PlaceCandidate(
                    place_id=item.place_id, name=item.name, category="已有行程地点",
                    address=item.address, estimated_cost=item.estimated_cost,
                )
    search: dict[str, Any] = {}
    if state.get("attraction_result") is not None:
        result = ActionResult.model_validate(state["attraction_result"])
        if result.status != "completed":
            raise PlanningError("景点搜索尚未成功，不能据此生成行程")
        parsed = PlaceSearchResult.model_validate(result.data)
        search = parsed.model_dump(mode="json")
        for candidate in parsed.candidates:
            places[candidate.place_id] = candidate.to_planning_candidate()
    return places, search


def normalize_draft(
    draft: Itinerary,
    places: Mapping[str, PlaceCandidate],
    request: TripRequest,
    days: int,
    current: Itinerary | None,
) -> None:
    """原地校验草稿结构，并用可信证据覆盖地点身份与费用。

    Args:
        draft: 模型提交的本轮草稿，校验通过后原地补齐事实字段。
        places: 候选及已有行程的可信地点索引。
        request: 旅行需求，日期按目的地当地日期处理。
        days: 期望天数，必须为正整数。
        current: 已有行程，需求未给日期时允许复用其日期。

    Raises:
        PlanningError: 天数、项目编号、地点来源或时间格式不符合约束。
    """
    if len(draft.days) != days or days < 1:
        raise PlanningError("草稿天数与旅行需求不一致")
    if [day.day_index for day in draft.days] != list(range(1, days + 1)):
        raise PlanningError("草稿天数编号必须从 1 连续递增")
    ids = set()
    for day in draft.days:
        if not day.items:
            raise PlanningError("草稿每天至少需要一个行程地点")
        day.date = (request.start_date + timedelta(days=day.day_index - 1)
                    if request.start_date else
                    current.days[day.day_index - 1].date
                    if current and day.day_index <= len(current.days) else None)
        day.walking_distance_km = 0
        day.warnings = ["停留时长与开始时间为规划建议，营业时间及预约要求需另行核实。"]
        for item in day.items:
            if item.place_id not in places:
                raise PlanningError("模型安排了候选及已有行程之外的地点，请先搜索新增地点")
            if not item.item_id.strip() or item.item_id in ids:
                raise PlanningError("行程项目编号为空或重复")
            if item.start_time.tzinfo is not None:
                raise PlanningError("行程时间必须为目的地当地时间，不带时区")
            ids.add(item.item_id)
            place = places[item.place_id]
            # 身份、地址、费用只复制输入证据，覆盖模型可能编造的数值。
            item.name, item.address = place.name, place.address
            item.estimated_cost = place.estimated_cost
            item.travel_from_previous_minutes = 0
        day.total_cost = float(sum((Decimal(str(item.estimated_cost))
                                    for item in day.items if item.estimated_cost is not None), Decimal(0)))
    draft.currency = "CNY"


def verify_routes(
    draft: Itinerary,
    selections: Sequence[_RouteSelection],
    records: Mapping[str, dict[str, Any]],
) -> list[RouteInfo]:
    """验收路线调用证据，回填交通耗时与步行距离。

    Args:
        draft: 已规范化的草稿，不在本函数中调整模型安排的开始时间。
        selections: 模型为每日相邻项目选择的路线调用编号。
        records: 本轮成功的工具轨迹；第三方工具结果保留原始字典边界。

    Returns:
        与草稿相邻项目一一对应的路线，距离单位为公里、耗时单位为分钟。

    Raises:
        PlanningError: 路线证据缺失、地点不匹配、数值无效或时间冲突。
    """
    selected = {(selection.from_item_id, selection.to_item_id): selection for selection in selections}
    expected = {(previous.item_id, item.item_id) for day in draft.days
                for previous, item in zip(day.items, day.items[1:])}
    if len(selected) != len(selections) or set(selected) != expected:
        raise PlanningError("最终草稿缺少相邻路线证据或包含无关路线")
    routes = []
    for day in draft.days:
        previous = None
        for item in day.items:
            start = datetime.combine(datetime.min, item.start_time)
            if previous is not None:
                selection = selected[(previous.item_id, item.item_id)]
                record = records.get(selection.tool_call_id)
                if not record or record["tool"] != "calculate_route":
                    raise PlanningError("路线引用没有成功的工具调用证据")
                args = record["arguments"]
                if (args["origin"]["place_id"] != previous.place_id
                        or args["destination"]["place_id"] != item.place_id):
                    raise PlanningError("路线工具的起终点与最终行程不一致")
                data = record["result"]["data"]
                distance, duration = data.get("distance_km"), data.get("duration_minutes")
                if any(isinstance(value, bool) or not isinstance(value, (int, float))
                       or not math.isfinite(value) or value < 0 for value in (distance, duration)):
                    raise PlanningError("路线工具未返回有效的距离和耗时")
                route = RouteInfo(from_item_id=previous.item_id, to_item_id=item.item_id,
                                  distance_km=distance, duration_minutes=math.ceil(duration),
                                  mode=args["mode"], provider=data["provider"])
                routes.append(route)
                item.travel_from_previous_minutes = route.duration_minutes
                earliest = datetime.combine(datetime.min, previous.start_time) + timedelta(
                    minutes=previous.duration_minutes + route.duration_minutes)
                if start < earliest:
                    raise PlanningError("模型没有按真实路线耗时调整行程，存在时间冲突")
                if route.mode == "walking":
                    day.walking_distance_km += route.distance_km
            end = start + timedelta(minutes=item.duration_minutes)
            if end.date() != datetime.min.date():
                raise PlanningError("行程跨越午夜，请减少当日地点或停留时长")
            previous = item
    return routes


def verify_cost(
    draft: Itinerary, cost_call_id: str, records: Mapping[str, dict[str, Any]],
) -> ActionResult:
    """核验费用证据与最终草稿逐项一致，未知金额保持为空。

    Args:
        draft: 已绑定真实地点价格的最终草稿。
        cost_call_id: 模型选用的费用工具调用编号。
        records: 仅包含本轮成功调用的工具账本。

    Returns:
        已验证的费用工具结果，保留已知小计与未知项目。

    Raises:
        PlanningError: 无成功费用证据或其项目与最终草稿不一致。
    """
    record = records.get(cost_call_id)
    if not record or record["tool"] != "estimate_itinerary_cost":
        raise PlanningError("最终草稿缺少成功的费用工具调用证据")
    expected = [{"description": item.place_id, "amount": item.estimated_cost}
                for day in draft.days for item in day.items]
    if _cost_signature(record["arguments"]["items"]) != _cost_signature(expected):
        raise PlanningError("费用工具结果与最终草稿不一致，请重新估算")
    cost = ActionResult.model_validate(record["result"])
    return cost
