from __future__ import annotations

from datetime import datetime, timezone

from langgraph.runtime import Runtime

from app.agents.context import TripGraphContext
from app.agents.state import TripGraphState
from app.core.logger import logger, node_log


def _budget(state: TripGraphState) -> float | None:
    if state.get("change_request") and state["change_request"].budget is not None:
        return state["change_request"].budget
    return state.get("trip_request").budget if state.get("trip_request") else None


@node_log
def validate_itinerary(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    result = runtime.context.validator.validate(
        state["draft_itinerary"], state.get("route_info", []), _budget(state)
    )
    if result.checked_at is None:
        result = result.model_copy(update={"checked_at": datetime.now(timezone.utc)})
    logger.info("行程校验完成: 通过={}, 问题数={}", result.passed, len(result.issues))
    return {"validation_result": result}


def validation_router(state: TripGraphState) -> str:
    result = state["validation_result"]
    if result.passed or all(issue.severity == "warning" for issue in result.issues):
        decision = "save"
    elif state.get("retry_count", 0) < 2:
        decision = "repair"
    else:
        decision = "unresolved"
    logger.bind(run_id=state.get("run_id") or "-").info(
        "校验结果路由: {}, 已重试={}", decision, state.get("retry_count", 0)
    )
    return decision


@node_log
async def repair_itinerary(
    state: TripGraphState,
    runtime: Runtime[TripGraphContext],
) -> dict:
    return {
        "draft_itinerary": await runtime.context.llm.repair_itinerary(
            state["draft_itinerary"], state["validation_result"]
        ),
        "retry_count": state.get("retry_count", 0) + 1,
    }
