from typing import Literal, TypedDict

from app.models.schemas import (
    Itinerary,
    PlaceCandidate,
    RouteInfo,
    TripChangeRequest,
    TripRequest,
    ValidationResult,
)


class TripGraphState(TypedDict, total=False):
    """一次旅行规划流程中的临时状态。"""

    # 当前这次 Graph 执行的唯一编号，用于日志追踪
    run_id: str

    # 当前旅行编号；创建新旅行时可以为空
    trip_id: int | None

    # 用户本次发送的原始消息
    user_message: str

    # 用户当前操作类型
    intent: Literal["create", "revise"] | None

    # 本次流程使用的旅行需求
    trip_request: TripRequest | None

    # 用户对已有行程的修改要求
    change_request: TripChangeRequest | None

    # 解析需求后仍然缺少的必要信息
    missing_fields: list[str]

    # 当前数据库中保存的行程版本号
    current_version_no: int | None

    # 当前已经保存的行程，只读，不应该被节点直接修改
    current_itinerary: Itinerary | None

    # 查询得到的候选景点
    candidate_places: list[PlaceCandidate]

    # 本次流程生成或修改中的行程草稿
    draft_itinerary: Itinerary | None

    # 高德返回的路线信息
    route_info: list[RouteInfo]

    # 对行程草稿的校验结果
    validation_result: ValidationResult | None

    # 当前已经重试了多少次
    retry_count: int

    # 如果用户信息不完整，需要追问的问题
    pending_question: str | None

    # 保存成功后的新版本号
    saved_version_no: int | None

    # 返回给前端或用户的最终文本
    response: str | None

    # 本次流程发生的错误
    error: str | None
