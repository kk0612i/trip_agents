from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import logger, node_log


@node_log
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
    logger.info("行程版本保存完成: trip_id={}, 版本={}", trip_id, version_no)
    return {"trip_id": trip_id, "saved_version_no": version_no}
