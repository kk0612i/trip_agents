"""主管检查点中的纯业务状态；外部资源仅存放于运行 Context。"""

from operator import add
from typing import Annotated, Any, Literal, TypedDict

from app.schemas.trip_schema import Itinerary, RouteInfo, TripRequest, ValidationResult
from app.schemas.agent_schema import SupervisorDecision, ActionResult


class AgentRunState(TypedDict, total=False):
    """Supervisor 循环使用的状态；运行时依赖通过 Context 注入。"""

    # 业务输入和待校验的行程数据
    trip_id: int | None  # 已保存旅行编号；新旅行为 None。
    user_message: str  # 当前轮次用户输入。
    trip_request: TripRequest | None  # 合并后的旅行需求；尚未解析时可为空。
    current_itinerary: Itinerary | None  # 已加载的正式版本快照；无历史版本时为空。
    draft_itinerary: Itinerary | None  # 本轮待校验草稿；不得隐式沿用上轮草稿。
    route_info: list[RouteInfo]  # 与当前草稿对应的路线证据列表。
    validation_result: ValidationResult | None  # 本轮确定性校验结果；草稿变更后失效。

    # 本轮专业任务结果
    attraction_result: ActionResult | None
    accommodation_result: ActionResult | None
    knowledge_result: ActionResult | None
    weather_result: ActionResult | None
    planner_result: ActionResult | None

    # 本轮运行状态，每次新用户消息重置
    run_id: str  # 本轮唯一编号，不跨用户消息复用。
    last_decision: SupervisorDecision | None  # 最近一次主管决策；初始化时为空。
    last_result: ActionResult | None  # 最近业务执行结果；不是可独立复用的保存凭证。
    steps: Annotated[list[dict[str, Any]], add]  # 每轮追加轨迹；新轮次通过 Overwrite 清空。
    iteration: int  # 本轮已消耗主管决策次数。
    agent_call_count: int  # 本轮已执行专业任务次数，失败调用也计入。
    tool_call_count: int  # 本轮公共工具调用次数，不含内部结果提交。
    retry_count: int  # 当前失败即退出，不做自动重试，因此保持为 0。
    response: str | None  # 面向用户的完成、追问或失败文案。
    error: str | None  # 可安全展示的失败原因；未失败时为空。

    # 解析和追问信息，便于 Supervisor 复用
    intent: str | None  # 当前合并的业务意图；尚未解析时为空。
    pending_question: str | None  # 需要用户补充的信息；无需追问时为空。
    status: Literal["running", "completed", "needs_input", "failed"]
    search_keywords: list[str]  # 本轮提取的检索关键词。
    missing_fields: list[str]  # 当前意图尚缺失的必填字段名。
    saved_version_no: int | None  # 本轮成功保存的版本号；尚未保存时为空。
    current_version_no: int | None  # 加载或成功保存的正式版本号。
    requirements_parsed: bool  # 本轮是否已完成需求解析。
    validation_fingerprint: str | None  # 通过校验时的输入摘要，用于拒绝陈旧校验凭证。
    saved_fingerprint: str | None  # 已保存输入摘要，用于拒绝重复保存。
    pending_update: dict[str, Any] | None  # 动作产生的待合并异构补丁；合并后清空。
