from __future__ import annotations

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import logger, node_log


@node_log
async def calculate_route(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    routes = await runtime.context.amap.calculate_route(state["draft_itinerary"])
    logger.info("路线计算完成: 路段数={}", len(routes))
    return {"route_info": routes}
