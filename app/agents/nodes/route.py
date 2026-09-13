from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState


async def calculate_route(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    return {
        "route_info": await runtime.context.amap.calculate_route(
            state["draft_itinerary"]
        ),
    }
