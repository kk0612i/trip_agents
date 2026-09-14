from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import node_log


@node_log
async def load_context(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    """读取当前版本；创建旅行时没有 trip_id，因此返回空上下文。"""

    trip_id = state.get("trip_id")
    if not trip_id:
        return {"current_version_no": None, "current_itinerary": None}

    current = await runtime.context.repository.load_current(trip_id)
    if current is None:
        return {
            "current_version_no": None,
            "current_itinerary": None,
            "error": f"旅行 {trip_id} 不存在",
        }

    version_no, itinerary = current
    return {
        "current_version_no": version_no,
        "current_itinerary": itinerary,
    }
