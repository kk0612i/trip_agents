from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.context import load_context
from app.agents.nodes.persistence import save_version
from app.agents.nodes.planning import (
    build_itinerary,
    revise_itinerary,
    search_places,
    search_places_for_revision,
)
from app.agents.nodes.request import (
    ask_user,
    check_complete,
    check_new_places,
    check_new_places_node,
    parse_request,
    route_intent,
    route_intent_node,
)
from app.agents.nodes.response import compose_response, compose_unresolved
from app.agents.nodes.route import calculate_route
from app.agents.nodes.validation import (
    repair_itinerary,
    validate_itinerary,
    validation_router,
)
from app.agents.state import TripGraphState
from app.agents.context import TripGraphContext


def build_trip_graph(*, checkpointer=None):
    """按流程图注册节点和边，返回可被 invoke/ainvoke 的 CompiledGraph。"""

    graph = StateGraph(
        TripGraphState,
        context_schema=TripGraphContext,
    )

    # 依赖由每次运行的 Runtime 上下文提供，节点本身不创建客户端。
    graph.add_node("load_context", load_context)
    graph.add_node("parse_request", parse_request)
    graph.add_node("ask_user", ask_user)
    graph.add_node("route_intent", route_intent_node)
    graph.add_node("search_places", search_places)
    graph.add_node("build_itinerary", build_itinerary)
    graph.add_node("check_new_places", check_new_places_node)
    graph.add_node("search_places_for_revision", search_places_for_revision)
    graph.add_node("revise_itinerary", revise_itinerary)
    graph.add_node("calculate_route", calculate_route)
    graph.add_node("validate_itinerary", validate_itinerary)
    graph.add_node("repair_itinerary", repair_itinerary)
    graph.add_node("save_version", save_version)
    graph.add_node("compose_response", compose_response)
    graph.add_node("compose_unresolved", compose_unresolved)

    graph.add_edge(START, "load_context")
    graph.add_edge("load_context", "parse_request")
    graph.add_conditional_edges(
        "parse_request",
        check_complete,
        {"incomplete": "ask_user", "error": "ask_user", "complete": "route_intent"},
    )
    graph.add_edge("ask_user", END)

    graph.add_conditional_edges(
        "route_intent",
        route_intent,
        {"create": "search_places", "revise": "check_new_places"},
    )
    graph.add_edge("search_places", "build_itinerary")
    graph.add_edge("build_itinerary", "calculate_route")
    graph.add_conditional_edges(
        "check_new_places",
        check_new_places,
        {"search": "search_places_for_revision", "reuse": "revise_itinerary"},
    )
    graph.add_edge("search_places_for_revision", "revise_itinerary")
    graph.add_edge("revise_itinerary", "calculate_route")
    graph.add_edge("calculate_route", "validate_itinerary")
    graph.add_conditional_edges(
        "validate_itinerary",
        validation_router,
        {"save": "save_version", "repair": "repair_itinerary", "unresolved": "compose_unresolved"},
    )
    graph.add_edge("repair_itinerary", "calculate_route")
    graph.add_edge("save_version", "compose_response")
    graph.add_edge("compose_response", END)
    graph.add_edge("compose_unresolved", END)

    return graph.compile(checkpointer=checkpointer)
