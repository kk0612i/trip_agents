"""行程确定性校验能力的应用层接口。"""

from __future__ import annotations

from app.models.schemas import Itinerary, RouteInfo, ValidationResult


class ValidationService:
    """使用 Python 规则检查时间、预算和路线约束。"""

    def validate(
        self,
        itinerary: Itinerary,
        routes: list[RouteInfo],
        budget: float | None,
    ) -> ValidationResult:
        pass
