"""搜索事实、筛选结果及搜索 Agent 的结构化输出。"""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.trip_schema import PlaceCandidate


class SearchPlaceCandidate(BaseModel):
    """高德搜索事实，不承载推荐时长、价格或室内属性推断。"""

    place_id: str
    name: str
    category: str
    address: str | None = None
    city: str | None = None
    longitude: float | None = None  # 来源经度，单位为角度；缺失时不由模型推断。
    latitude: float | None = None  # 来源纬度，单位为角度；缺失时不由模型推断。
    source: str = "amap"
    ticket_price: float | None = None  # 已知门票金额，单位为人民币；None 不表示免费。
    opening_hours: str | None = None  # 有来源证据的营业时间；未核实时为空。
    indoor: bool | None = None  # 是否室内；None 表示尚无证据。

    def to_planning_candidate(self) -> PlaceCandidate:
        """只复制来源事实；规划属性由后续明确的证据或用户输入补充。"""
        return PlaceCandidate(
            place_id=self.place_id, amap_poi_id=self.place_id, name=self.name,
            category=self.category, address=self.address, city=self.city,
            longitude=self.longitude, latitude=self.latitude, source=self.source,
            estimated_cost=self.ticket_price,
        )


# 单次实际搜索调用的安全轨迹；类说明用注释保持原模型 JSON Schema 描述。
class SearchToolTrace(BaseModel):
    keyword: str
    city: str | None = None
    limit: int = 10
    result_count: int = 0
    error: str | None = None  # 可公开的失败说明；成功时为空，不携带上游原始错误。


# 本轮真实候选的推荐理由，不包含模型自行生成的价格或营业事实。
class PlaceRecommendation(BaseModel):
    place_id: str
    reason: str


# 专业搜索结果及已执行轨迹；失败时仍保留已消耗的调用额度。
class PlaceSearchResult(BaseModel):
    candidates: list[SearchPlaceCandidate] = Field(default_factory=list)
    recommendations: list[PlaceRecommendation] = Field(default_factory=list)
    unmet_conditions: list[str] = Field(default_factory=list)
    tool_calls: list[SearchToolTrace] = Field(default_factory=list)
    tool_call_count: int = 0  # 已执行的公共工具调用数，含失败调用，不含内部结果提交。
    limit_reached: bool = False  # 是否已耗尽本轮搜索与详情共用的调用额度。
    summary: str = ""


class _SearchDecision(BaseModel):
    """提交最终景点筛选结果，不执行搜索。"""

    model_config = ConfigDict(extra="forbid")

    selected_place_ids: list[str] = Field(
        description="从已返回的搜索候选中选择的高德 POI 编号。"
    )
    unmet_condition_indexes: list[int] = Field(
        default_factory=list,
        description="未满足条件在输入 conditions 列表中的下标。",
    )
