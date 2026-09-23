"""确定性费用汇总；未知价格不会隐式变成免费。"""

from langchain.tools import ToolRuntime, tool

from app.schemas.agent_schema import ActionResult
from app.schemas.trip_schema import Itinerary, TripRequest
from app.services.cost_service import summarize_itinerary_cost
from app.tools.context import ToolContext
from app.tools.execution import guarded_tool
from app.schemas.tool_schema import CostItem, EstimateItineraryCostArguments


@tool(args_schema=EstimateItineraryCostArguments, response_format="content_and_artifact")
@guarded_tool("estimate_itinerary_cost")
async def estimate_itinerary_cost(
    runtime: ToolRuntime[ToolContext],
    items: list[CostItem],
    budget: float | None,
    currency: str,
) -> ActionResult:
    """汇总已提供费用；有未知费用时不判断是否在预算内。"""
    request = TripRequest.model_validate(runtime.context.state.get("trip_request") or {})
    effective_budget = request.budget if request.budget is not None else budget
    costs = [CostItem.model_validate(item) for item in items]
    # 只有未提交显式费用项时才读取草稿，保持既有参数优先级和校验时机。
    raw_draft = runtime.context.state.get("draft_itinerary") if not costs else None
    draft = Itinerary.model_validate(raw_draft) if raw_draft is not None else None
    return ActionResult(data=summarize_itinerary_cost(costs, effective_budget, currency, draft))
