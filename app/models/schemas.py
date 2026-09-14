from datetime import date as date_type, datetime, time
from typing import Literal

from pydantic import BaseModel, Field


class TripRequest(BaseModel):
    """用户创建旅行时提交的需求。"""

    # 旅行目的地，例如：长沙
    # 需求解析阶段可能暂时缺少目的地；此时由 ParsedTripRequest.missing_fields
    # 告知上游需要继续追问，真正生成行程前再校验该字段不能为空。
    destination: str | None = None

    # 出发城市，可选；后续可以用于计算交通方案
    origin: str | None = None

    # 出发日期，可选；没有日期时按“第 1 天、第 2 天”生成
    start_date: date_type | None = None

    # 旅行天数，限制在 1 到 14 天之间
    # 用户没有提供旅行天数时，解析器会返回 null 并记录到 missing_fields。
    days: int | None = Field(default=None, ge=1, le=14)

    # 预算上限，单位为人民币；不填写表示暂不限制预算
    budget: float | None = Field(default=None, ge=0)

    # 出行人数
    traveler_count: int = Field(default=1, ge=1)

    # 用户喜欢的内容，例如：美食、历史、自然风景
    preferences: list[str] = Field(default_factory=list)

    # 用户的限制条件，例如：不要太累、少走路
    constraints: list[str] = Field(default_factory=list)

    # 用户期望的旅行节奏
    pace: Literal["relaxed", "balanced", "compact"] = "balanced"


class TripChangeRequest(BaseModel):
    """用户对已有行程提出的修改要求。"""

    # 用户原始的修改文本
    raw_text: str

    # 需要从行程中删除的景点名称
    remove_places: list[str] = Field(default_factory=list)

    # 用户希望增加的景点关键词
    add_place_keywords: list[str] = Field(default_factory=list)

    # 修改后的预算上限
    budget: float | None = None

    # 需要增加的偏好，例如：增加本地小吃
    preferences_to_add: list[str] = Field(default_factory=list)

    # 需要增加的限制，例如：每天 18 点前结束
    constraints_to_add: list[str] = Field(default_factory=list)


class ParsedTripRequest(BaseModel):
    """LLM 对用户消息解析后的结构化结果。"""

    intent: Literal["create", "revise"]
    trip_request: TripRequest | None = None
    change_request: TripChangeRequest | None = None
    missing_fields: list[str] = Field(default_factory=list)


class PlaceCandidate(BaseModel):
    """地图服务或本地数据筛选出的候选地点。"""

    # 系统内部使用的地点编号
    place_id: str

    # 地点名称，例如：湖南省博物馆
    name: str

    # 地点类型，例如：博物馆、餐厅、景点
    category: str

    # 地点地址
    address: str | None = None

    # 高德返回的 POI 编号
    amap_poi_id: str | None = None

    # 地点纬度
    latitude: float | None = None

    # 地点经度
    longitude: float | None = None

    # 建议游玩时长，单位为分钟
    recommended_duration_minutes: int

    # 预计花费，例如门票价格
    estimated_cost: float = 0

    # 地点标签，例如：室内、亲子、历史
    tags: list[str] = Field(default_factory=list)

    # 地点信息来源地址
    source_url: str | None = None


class ItineraryItem(BaseModel):
    """某一天中的一个行程地点。"""

    # 当前行程项目的编号
    item_id: str

    # 对应的地点编号
    place_id: str

    # 地点名称
    name: str

    # 计划开始时间
    start_time: time

    # 预计停留时长，单位为分钟
    duration_minutes: int = Field(gt=0)

    # 从上一个地点到当前地点的交通时间，单位为分钟
    travel_from_previous_minutes: int = Field(default=0, ge=0)

    # 当前地点的预计花费
    estimated_cost: float = Field(default=0, ge=0)

    # 地点地址
    address: str | None = None

    # 给用户展示的补充说明
    notes: str = ""


class ItineraryDay(BaseModel):
    """某一天的完整行程。"""

    # 第几天，从 1 开始
    day_index: int

    # 实际日期；没有出发日期时可以为空
    date: date_type | None = None

    # 当天安排的地点列表，顺序就是游玩顺序
    items: list[ItineraryItem]

    # 当天预计总花费
    total_cost: float = 0

    # 当天预计步行距离，单位为公里
    walking_distance_km: float = 0

    # 当天产生的提醒，例如：景点可能需要预约
    warnings: list[str] = Field(default_factory=list)


class Itinerary(BaseModel):
    """一份完整的旅行行程。"""

    # 对整份行程的简短说明
    summary: str

    # 每天的行程安排
    days: list[ItineraryDay]

    # 整份行程的预计总花费
    total_cost: float

    # 金额单位
    currency: str = "CNY"


class RouteInfo(BaseModel):
    """两个行程地点之间的路线信息。"""

    # 出发的行程项目编号
    from_item_id: str

    # 到达的行程项目编号
    to_item_id: str

    # 两个地点之间的距离，单位为公里
    distance_km: float

    # 预计交通时间，单位为分钟
    duration_minutes: int

    # 出行方式，例如：walking、transit、driving
    mode: str

    # 路线数据来源，例如：amap(高德地图)
    provider: str = "amap"


class ValidationIssue(BaseModel):
    """行程校验发现的一个问题。"""

    # error 会阻止保存，warning 只进行提示
    severity: Literal["error", "warning"]

    # 问题编码，例如：budget_exceeded、time_conflict
    code: str

    # 给用户看的问题描述
    message: str

    # 问题所在的第几天；全局问题可以为空
    day_index: int | None = None


class ValidationResult(BaseModel):
    """对行程进行校验后的结果。"""

    # 是否通过校验；没有 error 时为 True
    passed: bool

    # 所有错误和警告
    issues: list[ValidationIssue] = Field(default_factory=list)

    # 校验完成时间
    checked_at: datetime | None = None
