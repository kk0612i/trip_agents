"""需求解析输出与多轮需求补丁。"""

from datetime import date as date_type
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.trip_schema import TripChangeRequest, TripRequest


class ParsedTripRequest(BaseModel):
    """LLM 对用户消息解析后的结构化结果。"""

    intent: Literal["create", "revise"]
    trip_request: TripRequest | None = None
    change_request: TripChangeRequest | None = None
    missing_fields: list[str] = Field(default_factory=list)


class TripRequestPatch(BaseModel):
    """需求字段补丁；None 表示本轮未提供，避免覆盖已知需求。"""

    model_config = ConfigDict(extra="forbid")
    destination: str | None = None
    origin: str | None = None
    start_date: date_type | None = None
    days: int | None = Field(default=None, ge=1, le=14)
    budget: float | None = Field(default=None, ge=0)
    traveler_count: int | None = Field(default=None, ge=1)
    preferences: list[str] | None = None
    constraints: list[str] | None = None
    pace: Literal["relaxed", "balanced", "compact"] | None = None


# 简单需求 Agent 的模型输出；类说明用注释，保持冻结的模型 JSON Schema。
class MinimalParsedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    intent: Literal["create", "revise", "direct_search", "knowledge", "other"]
    trip_request: TripRequestPatch | None = None  # 本轮需求补丁；None 表示无需求字段更新。
    search_keywords: list[str] = Field(default_factory=list)  # 从用户消息提取的候选搜索词。
    missing_fields: list[str] = Field(default_factory=list)  # 当前意图需要继续追问的需求字段名。
