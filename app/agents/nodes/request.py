from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import node_log, logger


@node_log
async def parse_request(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    try:
        parsed = await runtime.context.llm.parse_request(
            state["user_message"], state.get("current_itinerary")
        )
    except Exception as e:
        logger.exception("解析请求节点异常")
        return {
            "error": str(e),
        }
    return {
        "intent": parsed.intent,
        "trip_request": parsed.trip_request,
        "change_request": parsed.change_request,
        "missing_fields": parsed.missing_fields,
    }


def check_complete(state: TripGraphState) -> str:
    """检查信息是否完整,不完整或错误则询问用户,完整则进入意图路由节点"""
    if state.get("error"):
        return "error"
    if state.get("missing_fields"):
        return "incomplete"
    if state.get("intent") == "revise" and not state.get("current_itinerary"):
        return "error"
    return "complete"


def route_intent(state: TripGraphState) -> str:
    """意图路由节点,分别路由到创建旅行、修改旅行"""
    # TODO 后续需新增 query 节点, 用于景点资料查询
    return "create" if state.get("intent") == "create" else "revise"


@node_log
def route_intent_node(state: TripGraphState) -> dict:
    """流程图中的路由节点只触发条件边，不修改业务状态。"""

    return {}


def check_new_places(state: TripGraphState) -> str:
    change = state.get("change_request")
    if change and change.add_place_keywords:
        return "search"
    return "reuse"


@node_log
def check_new_places_node(state: TripGraphState) -> dict:
    """流程图中的判断节点只触发条件边，不修改业务状态。"""

    return {}


@node_log
def ask_user(state: TripGraphState) -> dict:
    if state.get("error"):
        return {"pending_question": None, "response": state["error"]}
    fields = "、".join(state.get("missing_fields", []))
    return {"pending_question": f"请补充：{fields}", "response": f"请补充：{fields}"}
