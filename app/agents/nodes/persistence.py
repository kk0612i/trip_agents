from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState


async def save_version(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    trip_id, version_no = await runtime.context.repository.save_version(
        state.get("trip_id"),
        state.get("trip_request"),
        state.get("change_request"),
        state["draft_itinerary"],
        state.get("route_info", []),
        state["validation_result"],
    )
    return {"trip_id": trip_id, "saved_version_no": version_no}
