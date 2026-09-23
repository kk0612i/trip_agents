"""前端 HTTP/SSE 契约，不直接暴露 Agent State，也不执行业务或数据库操作。

公开 DTO 支持未知费用；内部 Planner/Validator 的保存限制仍由原有业务模型负责。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Annotated, Any, Generic, Literal, Self, TypeVar
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator


def _uuid(value: Any) -> str:
    """校验外部编号并统一为标准 UUID 文本。"""
    if not isinstance(value, str):
        raise ValueError("编号必须为 UUID 字符串")
    try:
        return str(UUID(value.strip()))
    except ValueError as exc:
        raise ValueError("编号必须为有效 UUID") from exc


def _trip_id(value: Any) -> str:
    """校验公开旅行编号，避免 BIGINT 经前端浮点数转换丢失精度。"""
    if not isinstance(value, str):
        raise ValueError("旅行编号必须为正整数的十进制字符串")
    value = value.strip()
    if not re.fullmatch(r"[1-9][0-9]{0,19}", value) or int(value) > 2**64 - 1:
        raise ValueError("旅行编号超出 BIGINT UNSIGNED 范围")
    return value


def _trim(value: Any) -> Any:
    """仅清理文本边缘空白，其他类型交由后续严格校验拒绝。"""
    return value.strip() if isinstance(value, str) else value


def _email(value: Any) -> str:
    """规范化正式 API 契约邮箱。

    Args:
        value: Pydantic 前置校验收到的原始输入，可能不是字符串。

    Returns:
        去除首尾空白并转为小写、且通过格式及长度校验的邮箱。

    Raises:
        ValueError: 输入不是字符串，或邮箱格式及长度不符合公开契约。
    """
    if not isinstance(value, str):
        raise ValueError("邮箱必须为字符串")
    # 邮箱的规范化文本；用于统一账号查询及唯一性判断，长度上限为 254 个字符。
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("请输入有效的邮箱地址")
    return value


def _number(value: Any) -> float:
    """接纳有限数值并转为浮点数，拒绝布尔值和数字字符串。"""
    # bool 是 int 的子类，必须显式拒绝；不接受字符串数字。
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise ValueError("必须为有限数值")
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError("必须为有限数值") from exc
    if not math.isfinite(number):
        raise ValueError("必须为有限数值")
    return number


def _money(value: Any) -> float:
    """校验人民币金额的非负、两位小数及数据库精度边界。"""
    number = _number(value)
    amount = Decimal(str(value))
    if not 0 <= amount <= Decimal("9999999999.99") or amount % Decimal("0.01"):
        raise ValueError("金额必须为非负数，最多两位小数且不超过 DECIMAL(12,2)")
    return number


def _timestamp(value: Any) -> datetime:
    """将带时区的外部时间规范为 UTC，拒绝缺少时区的时间。"""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("时间戳必须为带时区的 ISO 8601 时间") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间戳必须携带时区；数据库 UTC 时间需在投影时附加时区")
    return value.astimezone(timezone.utc)


def _date(value: Any) -> date:
    """接纳日历日期对象或 YYYY-MM-DD 文本，不隐式截断时间戳。"""
    if type(value) is date:
        return value
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    raise ValueError("日期必须为 YYYY-MM-DD")


def _time(value: Any) -> time:
    """校验行程当地时刻，只接受无时区且精确到秒的时间。"""
    if isinstance(value, str) and re.fullmatch(r"\d{2}:\d{2}:\d{2}", value):
        value = time.fromisoformat(value)
    if isinstance(value, time) and value.tzinfo is None and value.microsecond == 0:
        return value
    raise ValueError("行程时间必须为无时区的 HH:mm:ss")


UUIDString = Annotated[str, BeforeValidator(_uuid)]
TripId = Annotated[str, BeforeValidator(_trip_id)]
NonEmptyText = Annotated[str, Field(strict=True, min_length=1), BeforeValidator(_trim)]
Email = Annotated[str, BeforeValidator(_email)]
PositiveInt = Annotated[int, Field(strict=True, ge=1, le=2**32 - 1)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0, le=2**32 - 1)]
Money = Annotated[float, BeforeValidator(_money)]
NonNegativeNumber = Annotated[float, Field(ge=0), BeforeValidator(_number)]
UTCTimestamp = Annotated[datetime, BeforeValidator(_timestamp)]
TripDate = Annotated[date, BeforeValidator(_date)]
TripTime = Annotated[time, BeforeValidator(_time)]
RunStatus = Literal["queued", "running", "completed", "needs_input", "failed"]
RunIntent = Literal["create", "revise", "direct_search", "knowledge", "other"]
EventType = Literal["run.started", "progress", "run.completed", "run.needs_input", "run.failed"]
T = TypeVar("T")


# 公开 DTO 公共配置；以下类说明用注释保持既有 JSON Schema description 不变。
class APIModel(BaseModel):
    # 默认值同样规范化，保证“省略默认字段”和“显式默认值”的快照摘要一致。
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_default=True)


# 正式凭证契约，auth_schema 复用这些模型作为认证入口。
class Credentials(APIModel):
    email: Email
    # 密码不做 strip，首尾空格也是密码内容。
    password: str = Field(strict=True, min_length=8, max_length=128, repr=False)


# 正式账号的公开投影，只包含 UUID 和规范化邮箱，不暴露密码材料。
class UserView(APIModel):
    id: UUIDString
    email: Email


# 正式认证响应，包含 bearer 令牌和公开用户信息。
class AuthResponse(APIModel):
    access_token: NonEmptyText
    token_type: Literal["bearer"] = "bearer"
    user: UserView


# 创建旅行会话的请求；不含运行消息或后台调度参数。
class SessionCreate(APIModel):
    trip_id: TripId | None = None  # 绑定已有旅行；None 表示尚未保存的新旅行。


class PageQuery(APIModel):
    """校验已解析的查询参数；HTTP 适配层需先把 limit 的十进制文本解析为整数。"""

    limit: int = Field(default=20, strict=True, ge=1, le=100)
    cursor: NonEmptyText | None = None


# 统一游标分页结果；游标由存储实现提供，不保证可由客户端解析。
class Page(APIModel, Generic[T]):
    items: list[T]
    next_cursor: NonEmptyText | None = None  # None 表示没有后续页。


# 已合并的公开旅行需求；可空项表示用户尚未提供，不代表默认业务事实。
class TripRequestDTO(APIModel):
    destination: NonEmptyText | None = None
    origin: NonEmptyText | None = None
    start_date: TripDate | None = None
    days: Annotated[int, Field(strict=True, ge=1, le=14)] | None = None
    budget: Money | None = None  # 预算上限，单位为人民币；None 表示尚未指定。
    traveler_count: PositiveInt = 1
    preferences: list[NonEmptyText] = Field(default_factory=list)
    constraints: list[NonEmptyText] = Field(default_factory=list)
    pace: Literal["relaxed", "balanced", "compact"] = "balanced"


# 搜索候选事实投影；经纬度以角度表示，未知业务属性保持 null。
class SearchCandidateDTO(APIModel):
    place_id: NonEmptyText
    name: NonEmptyText
    category: NonEmptyText
    address: str | None = None
    city: NonEmptyText
    longitude: Annotated[float, Field(ge=-180, le=180), BeforeValidator(_number)]
    latitude: Annotated[float, Field(ge=-90, le=90), BeforeValidator(_number)]
    source: Literal["amap", "demo"]
    # 当前服务没有可靠价格/营业时间/室内属性，不能由模型推测补齐。
    estimated_cost: None = None
    opening_hours: None = None
    indoor: None = None
    image_url: str | None = None
    image_credit: str | None = None


# 对本轮候选的推荐说明，只能引用 SearchResultDTO 中实际存在的地点。
class RecommendationDTO(APIModel):
    place_id: NonEmptyText
    reason: NonEmptyText


# 本轮搜索结果，包含真实候选、推荐和未核验条件。
class SearchResultDTO(APIModel):
    candidates: list[SearchCandidateDTO] = Field(default_factory=list)
    recommendations: list[RecommendationDTO] = Field(default_factory=list)
    data_sources: list[str] = Field(default_factory=list)
    unmet_conditions: list[str] = Field(default_factory=list)
    summary: str = ""
    limit_reached: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        """拒绝重复候选和引用本轮候选范围之外的推荐。

        Returns:
            引用一致的当前搜索结果。

        Raises:
            ValueError: 候选编号重复或推荐引用未知候选。
        """
        ids = {item.place_id for item in self.candidates}
        if len(ids) != len(self.candidates) or any(item.place_id not in ids for item in self.recommendations):
            raise ValueError("候选编号必须唯一，推荐只能引用本次候选")
        return self


# 单个行程项目；停留和交通时长单位为分钟，费用单位为人民币。
class ItineraryItemDTO(APIModel):
    item_id: NonEmptyText
    place_id: NonEmptyText
    name: NonEmptyText
    start_time: TripTime
    duration_minutes: PositiveInt
    travel_from_previous_minutes: NonNegativeInt = 0
    address: str | None = None
    notes: str = ""
    estimated_cost: Money | None = None  # None 表示价格未知，不等同于免费。


# 单日行程，天序号从 1 开始；未指定出发日期时 date 可以为空。
class ItineraryDayDTO(APIModel):
    day_index: Annotated[int, Field(strict=True, ge=1, le=14)]
    date: TripDate | None = None
    items: list[ItineraryItemDTO]
    total_cost: Money | None = None
    walking_distance_km: NonNegativeNumber = 0  # 当日累计步行距离，单位为千米。
    warnings: list[str] = Field(default_factory=list)


# 公开完整行程；任何组成项价格未知时，相应汇总金额必须保持未知。
class ItineraryDTO(APIModel):
    summary: str
    days: list[ItineraryDayDTO] = Field(min_length=1, max_length=14)
    total_cost: Money | None = None
    currency: Literal["CNY"] = "CNY"

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        """校验天序号、项目引用和未知费用的逐层传播。

        Returns:
            结构及费用语义一致的当前行程。

        Raises:
            ValueError: 序号、项目编号、首段交通或费用汇总不一致。
        """
        if [day.day_index for day in self.days] != list(range(1, len(self.days) + 1)):
            raise ValueError("天序号必须从 1 连续递增")
        ids = [item.item_id for day in self.days for item in day.items]
        if len(ids) != len(set(ids)):
            raise ValueError("行程项目编号必须全程唯一")
        for day in self.days:
            if day.items and day.items[0].travel_from_previous_minutes != 0:
                raise ValueError("每天首个项目的前序交通时间必须为 0")
            if any(item.estimated_cost is None for item in day.items) and day.total_cost is not None:
                raise ValueError("有未知项目费用时，当日总费用必须为空")
        if any(day.total_cost is None for day in self.days) and self.total_cost is not None:
            raise ValueError("有未知日费用时，全程总费用必须为空")
        return self


# 行程项目之间的路线事实；距离为千米、时长为分钟，不含原始坐标。
class RouteDTO(APIModel):
    from_item_id: NonEmptyText
    to_item_id: NonEmptyText
    distance_km: NonNegativeNumber
    duration_minutes: NonNegativeInt
    mode: Literal["walking", "driving"]
    provider: NonEmptyText = "amap"


# 行程校验发现的问题；day_index 为空表示不归属特定日期。
class ValidationIssueDTO(APIModel):
    severity: Literal["error", "warning"]
    code: NonEmptyText
    message: NonEmptyText
    day_index: PositiveInt | None = None


# 确定性校验结果与检查时间，不等同于持久化成功凭证。
class ValidationDTO(APIModel):
    passed: bool = Field(strict=True)
    issues: list[ValidationIssueDTO] = Field(default_factory=list)
    checked_at: UTCTimestamp

    @model_validator(mode="after")
    def validate_passed(self) -> Self:
        """确保通过标记与错误级别问题一致。

        Returns:
            标记一致的当前校验结果。

        Raises:
            ValueError: passed 与是否存在 error 级问题矛盾。
        """
        if self.passed != (not any(issue.severity == "error" for issue in self.issues)):
            raise ValueError("passed 必须与 error 级别问题一致")
        return self


# 可公开的稳定错误结构；details 仅放安全业务上下文，不放上游原始响应。
class PublicError(APIModel):
    code: NonEmptyText
    message: NonEmptyText
    retryable: bool = Field(default=False, strict=True)
    details: dict[str, Any] = Field(default_factory=dict)


# HTTP 错误包装；request_id 与响应头和日志编号保持一致。
class ErrorResponse(APIModel):
    request_id: UUIDString
    error: PublicError


# 已成功保存的旅行版本引用；当前保存占位接口不会产生此结果。
class SavedVersion(APIModel):
    trip_id: TripId
    version_no: PositiveInt


# 一次运行的公开业务快照；None 表示该能力尚未取得结果。
class RunResult(APIModel):
    trip_request: TripRequestDTO | None = None
    search: SearchResultDTO | None = None
    itinerary: ItineraryDTO | None = None
    routes: list[RouteDTO] | None = None
    validation: ValidationDTO | None = None
    saved_version: SavedVersion | None = None

    @model_validator(mode="after")
    def validate_saved_snapshot(self) -> Self:
        """校验保存快照完整性，并确保路线引用当前行程项目。

        Returns:
            内部引用一致的当前运行结果。

        Raises:
            ValueError: 保存快照不完整、校验未通过或路线引用无效。
        """
        if self.saved_version is not None and (
            self.trip_request is None or self.itinerary is None or self.routes is None
            or self.validation is None or not self.validation.passed
        ):
            raise ValueError("已保存版本必须包含需求、行程、路线和通过的校验")
        if self.routes is not None:
            if self.itinerary is None:
                raise ValueError("路线必须关联行程")
            ids = {item.item_id for day in self.itinerary.days for item in day.items}
            if any(route.from_item_id not in ids or route.to_item_id not in ids for route in self.routes):
                raise ValueError("路线端点必须引用行程项目")
        return self


# 客户端提交的一条逻辑消息；请求 UUID 用于未来的幂等受理。
class RunSubmission(APIModel):
    client_request_id: UUIDString
    message: Annotated[str, Field(strict=True, min_length=1, max_length=4000), BeforeValidator(_trim)]
    expected_version_no: PositiveInt | None = None  # 客户端预期基线；None 表示未提交版本约束。

    def request_hash(self) -> str:
        """幂等键单独存储；摘要只覆盖规范化消息和期望版本。"""
        return content_hash(self.model_dump(mode="json", exclude={"client_request_id"}))


# 异步受理后的轮询和事件入口；当前 501 骨架不会伪造此成功凭据。
class RunReceipt(APIModel):
    run_id: UUIDString
    session_id: UUIDString
    status: RunStatus
    status_url: NonEmptyText
    events_url: NonEmptyText


# 单次运行公开状态；时间均为带 UTC 时区的时间，未到达的阶段时间为空。
class RunView(APIModel):
    run_id: UUIDString
    session_id: UUIDString
    client_request_id: UUIDString
    message: Annotated[str, Field(strict=True, min_length=1, max_length=4000), BeforeValidator(_trim)]
    status: RunStatus
    intent: RunIntent | None = None
    base_version_no: PositiveInt | None = None
    response: str | None = None
    pending_question: NonEmptyText | None = None
    missing_fields: list[NonEmptyText] = Field(default_factory=list)
    result: RunResult = Field(default_factory=RunResult)
    error: PublicError | None = None
    created_at: UTCTimestamp
    started_at: UTCTimestamp | None = None
    finished_at: UTCTimestamp | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        """校验状态流转对应的时间、公开结果及终态必要字段。

        Returns:
            字段与运行状态一致的当前视图。

        Raises:
            ValueError: 状态、时间顺序、终态内容或保存结果相互矛盾。
        """
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("开始时间不能早于受理时间")
        if self.finished_at is not None and self.finished_at < (self.started_at or self.created_at):
            raise ValueError("结束时间不能早于开始或受理时间")
        if self.status == "queued" and self.started_at is not None:
            raise ValueError("排队状态不能有开始时间")
        if self.status == "running" and self.started_at is None:
            raise ValueError("运行中必须有开始时间")
        if self.status in {"queued", "running"}:
            if (any(value is not None for value in (self.response, self.pending_question, self.finished_at, self.error))
                    or self.missing_fields or self.result != RunResult()):
                raise ValueError("非终态不能提前发布结果、回答或错误")
            return self
        if self.finished_at is None or self.response is None:
            raise ValueError("终态必须包含结束时间和面向用户的回答")
        if self.status != "failed" and self.started_at is None:
            raise ValueError("成功或追问状态必须经过运行阶段")
        if (self.status == "failed") != (self.error is not None):
            raise ValueError("只有失败终态必须携带错误")
        if (self.status == "needs_input") != (self.pending_question is not None):
            raise ValueError("只有追问终态必须携带非空问题")
        if self.status == "completed":
            if self.intent in {"create", "revise"} and self.result.saved_version is None:
                raise ValueError("创建或修改完成时必须已保存版本")
            if self.intent == "direct_search" and self.result.search is None:
                raise ValueError("搜索完成时必须有搜索结果，空搜索也要返回对象")
        return self


# 会话详情；首次保存前旅行及版本为空，无待执行运行时 active_run_id 为空。
class SessionView(APIModel):
    session_id: UUIDString
    trip_id: TripId | None = None
    current_version_no: PositiveInt | None = None
    trip_request: TripRequestDTO | None = None
    latest_run_id: UUIDString | None = None
    active_run_id: UUIDString | None = None
    pending_question: NonEmptyText | None = None
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


# 会话列表摘要，不包含模型轨迹或内部运行上下文。
class SessionSummary(APIModel):
    session_id: UUIDString
    title: NonEmptyText
    trip_id: TripId | None = None
    current_version_no: PositiveInt | None = None
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


# 旅行主记录的公开投影，通过 URL 指向当前正式版本。
class TripView(APIModel):
    trip_id: TripId
    current_version_no: PositiveInt
    current_version_url: NonEmptyText
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


# 不可变历史版本的列表摘要，记录其来源运行用于追溯。
class VersionSummary(APIModel):
    trip_id: TripId
    version_no: PositiveInt
    source_run_id: UUIDString
    summary: str
    created_at: UTCTimestamp


# 已保存版本的完整快照；需求、行程、路线及校验共同组成内容摘要。
class ItineraryVersion(APIModel):
    trip_id: TripId
    version_no: PositiveInt
    source_run_id: UUIDString
    trip_request: TripRequestDTO
    itinerary: ItineraryDTO
    routes: list[RouteDTO]
    validation: ValidationDTO
    created_at: UTCTimestamp

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        """复用运行结果契约检查持久化版本快照。

        Returns:
            已完成完整性校验的当前版本。

        Raises:
            ValueError: 快照缺少通过的校验结果或包含无效路线引用。
        """
        RunResult(trip_request=self.trip_request, itinerary=self.itinerary, routes=self.routes,
                  validation=self.validation, saved_version=SavedVersion(trip_id=self.trip_id, version_no=self.version_no))
        return self

    def content_hash(self) -> str:
        """同一次保存重试复用原始快照，不能重新生成 checked_at 后声称内容相同。"""
        return content_hash(self.model_dump(mode="json", include={"trip_request", "itinerary", "routes", "validation"}))


# 运行中公开进度，描述当前业务阶段，不输出模型内部思考。
class Progress(APIModel):
    # queued 是前端本地提示，不是服务端 progress 事件阶段。
    stage: Literal["understand", "search", "plan", "validate", "save"]
    message: NonEmptyText


# 运行开始事件的数据体，仅声明已进入 running。
class RunStarted(APIModel):
    status: Literal["running"] = "running"


# 公开事件信封；seq 在同一运行内从 1 递增，用于断线续传定位。
class RunEvent(APIModel, Generic[T]):
    run_id: UUIDString
    seq: PositiveInt
    occurred_at: UTCTimestamp
    data: T


class SSEEvent(APIModel):
    """命名事件的校验包装；event 写入 SSE event 行，envelope 写入 data 行。"""

    event: EventType
    envelope: RunEvent[RunStarted | Progress | RunView]

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        """核对 SSE 事件名、数据类型和终态所属运行。

        Returns:
            事件名与数据一致的当前事件包装。

        Raises:
            ValueError: 事件名称、运行编号或终态内容不匹配。
        """
        data = self.envelope.data
        if self.event == "run.started":
            valid = isinstance(data, RunStarted)
        elif self.event == "progress":
            valid = isinstance(data, Progress)
        else:
            valid = (isinstance(data, RunView) and self.event == f"run.{data.status}"
                     and data.run_id == self.envelope.run_id)
        if not valid:
            raise ValueError("SSE 事件名称、运行编号与数据不匹配")
        return self

    @property
    def event_id(self) -> str:
        """生成 SSE id 行使用的运行编号与序号组合。"""
        return f"{self.envelope.run_id}:{self.envelope.seq}"


def content_hash(value: dict[str, Any]) -> str:
    """稳定 JSON 摘要：字段排序、保留数组顺序、UTF-8、无空白、不允许 NaN。"""
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
