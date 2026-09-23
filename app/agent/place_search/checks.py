"""搜索账本提取与最终推荐的证据边界检查。"""

from typing import Any

from pydantic import TypeAdapter, ValidationError

from app.schemas.agent_schema import ActionResult
from app.schemas.place_search_schema import (
    PlaceRecommendation, PlaceSearchResult, SearchPlaceCandidate, SearchToolTrace, _SearchDecision,
)
from app.schemas.trip_schema import TripRequest


def collect_search_evidence(
    records: list[dict[str, Any]], result: PlaceSearchResult, call_budget: int,
) -> tuple[dict[str, SearchPlaceCandidate], dict[str, list[str]]]:
    """从本轮新增账本恢复候选及关键词，并保留失败调用轨迹。

    Args:
        records: 当前 Agent 作用域中新产生的工具执行记录。
        result: 本轮输出；原地补充轨迹、调用次数和额度耗尽标记。
        call_budget: 搜索和详情共用的本轮调用上限。

    Returns:
        按 POI 编号去重的候选，以及各候选实际命中的搜索关键词。
    """
    candidates: dict[str, SearchPlaceCandidate] = {}
    evidence: dict[str, list[str]] = {}
    for record in records:
        if record["tool"] != "search_attractions" or record["status"] == "rejected":
            continue
        raw = record.get("result", {})
        try:
            found = TypeAdapter(list[SearchPlaceCandidate]).validate_python(raw["data"]["candidates"])
            error = None if record["status"] == "completed" else "高德搜索失败"
        except (KeyError, ValidationError):
            found, error = [], "搜索候选数据格式不正确"
        trace = SearchToolTrace(**record["arguments"], result_count=len(found), error=error)
        for poi in found:
            candidates.setdefault(poi.place_id, poi)
            evidence.setdefault(poi.place_id, []).append(trace.keyword)
        result.tool_calls.append(trace)
    result.tool_call_count = sum(record.get("status") != "rejected" for record in records)
    result.limit_reached = result.tool_call_count >= call_budget
    return candidates, evidence


def finalize_search_result(
    result: PlaceSearchResult,
    decision: _SearchDecision,
    request: TripRequest,
    conditions: list[str],
    candidates: dict[str, SearchPlaceCandidate],
    evidence: dict[str, list[str]],
    call_budget: int,
) -> ActionResult:
    """检查模型选择属于真实搜索证据，再生成可展示推荐。

    Args:
        result: 已收集本轮工具轨迹的结果，原地填充推荐和未核验条件。
        decision: 模型通过固定 _SearchDecision 协议提交的选择。
        request: 已校验的旅行需求。
        conditions: 交给模型的有序偏好与约束，用于复核索引。
        candidates: 本轮搜索获得的候选，不能使用历史状态补充。
        evidence: 候选对应的真实搜索关键词。
        call_budget: 本轮工具额度，用于说明是否停止补搜。

    Returns:
        成功推荐或明确失败；失败也保留已经执行的工具轨迹。
    """
    def failed(message: str) -> ActionResult:
        """生成验收失败结果，同时保留已消耗额度及真实搜索轨迹。"""
        return ActionResult(status="failed", message=message, data=result.model_dump(mode="json"))

    if not result.tool_calls:
        return failed("模型未调用搜索工具，无法提供景点证据")
    if not candidates and any(trace.error for trace in result.tool_calls):
        return failed("高德搜索失败，未取得可用候选")
    if any(pid not in candidates for pid in decision.selected_place_ids):
        return failed("模型选择了工具结果之外的景点")
    if any(index < 0 or index >= len(conditions) for index in decision.unmet_condition_indexes):
        return failed("模型返回了无效的条件编号")

    for pid in dict.fromkeys(decision.selected_place_ids):
        poi = candidates[pid]
        result.candidates.append(poi)
        # 说明只使用真实工具证据，避免把模型自由文本中的票价或室内属性当成事实。
        queries = "、".join(dict.fromkeys(evidence[pid]))
        result.recommendations.append(PlaceRecommendation(
            place_id=pid,
            reason=f"在“{queries}”搜索中返回；高德类别：{poi.category}，由模型筛选为候选。",
        ))
    # 这些属性不在基础 POI 的可靠字段中，必须显式标记为待核验。
    unmet = [conditions[index] for index in decision.unmet_condition_indexes]
    unmet.extend(f"尚未核验：{constraint}" for constraint in request.constraints)
    if request.budget is not None:
        unmet.append("门票价格未知，尚不能核验预算条件")
    unmet.append("基础 POI 不提供可靠门票、营业时间和室内属性，相关条件尚未核验")
    if not result.candidates:
        unmet.append("未找到符合需求的可推荐候选景点")
    if any(trace.error for trace in result.tool_calls):
        unmet.append("部分高德搜索失败，候选覆盖可能不足")
    if result.limit_reached:
        unmet.append(f"本次已用完 {call_budget} 次搜索额度，未继续补搜")
    result.unmet_conditions = list(dict.fromkeys(unmet))
    result.summary = f"已筛选 {len(result.candidates)} 个候选景点，相关条件仍需核实。"
    return ActionResult(message=result.summary, data=result.model_dump(mode="json"))
