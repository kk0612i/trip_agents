"""Planner 最终输出协议；类名是模型工具协议的一部分。"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.trip_schema import Itinerary


class _RouteSelection(BaseModel):
    """模型选择采用哪次路线查询；事实数值从执行账本读取。"""

    from_item_id: str
    to_item_id: str
    tool_call_id: str = Field(description="原样复制路线工具返回 JSON 中的 tool_call_id")


class _PlannerResult(BaseModel):
    """完成或结束规划时提交的结构化结果，不执行任何查询或写入。"""

    status: Literal["completed", "failed"] = "completed"
    draft_itinerary: Itinerary | None = None
    route_selections: list[_RouteSelection] = Field(default_factory=list)
    cost_call_id: str = Field(default="", description="原样复制费用工具返回 JSON 中的 tool_call_id")
