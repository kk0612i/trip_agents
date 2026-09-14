from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import node_log


@node_log
async def search_places(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    request = state.get("trip_request") or state.get("change_request")
    return {
        "candidate_places": await runtime.context.amap.search_places(request),
    }


@node_log
async def search_places_for_revision(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    return {
        "candidate_places": await runtime.context.amap.search_places(
            state["change_request"]
        ),
    }


@node_log
async def build_itinerary(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    return {
        "draft_itinerary": await runtime.context.llm.build_itinerary(
            state["trip_request"], state.get("candidate_places", [])
        ),
        "retry_count": 0,
    }


@node_log
async def revise_itinerary(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    return {
        "draft_itinerary": await runtime.context.llm.revise_itinerary(
            state["current_itinerary"],
            state["change_request"],
            state.get("candidate_places", []),
        ),
        "retry_count": state.get("retry_count", 0),
    }
