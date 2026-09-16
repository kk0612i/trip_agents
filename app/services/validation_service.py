"""行程确定性校验能力的应用层接口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models.schemas import Itinerary, RouteInfo, ValidationIssue, ValidationResult


class ValidationService:
    """使用 Python 规则检查时间、预算和路线约束。"""

    def validate(
        self,
        itinerary: Itinerary,
        routes: list[RouteInfo],
        budget: float | None,
    ) -> ValidationResult:
        issues: list[ValidationIssue] = []
        money_epsilon = 0.01

        def issue(severity: str, code: str, message: str, day_index: int | None = None) -> None:
            issues.append(
                ValidationIssue(
                    severity=severity, code=code, message=message, day_index=day_index
                )
            )

        # 以项目明细为准检查每天及整份行程的金额汇总，避免 LLM 输出的汇总字段漂移。
        calculated_total = 0.0
        item_ids: set[str] = set()
        route_durations = {(r.from_item_id, r.to_item_id): r.duration_minutes for r in routes}
        for expected_day_index, day in enumerate(itinerary.days, start=1):
            if day.day_index != expected_day_index:
                issue(
                    "error",
                    "invalid_day_index",
                    f"行程天数编号应从 1 连续递增，实际为第{day.day_index}天。",
                    day.day_index,
                )
            day_total = sum(item.estimated_cost for item in day.items)
            calculated_total += day_total
            if abs(day.total_cost - day_total) > money_epsilon:
                issue(
                    "error",
                    "day_total_cost_mismatch",
                    f"第{day.day_index}天汇总费用为 {day.total_cost:.2f}，项目费用合计为 {day_total:.2f}。",
                    day.day_index,
                )

            previous = None
            for item in day.items:
                if item.item_id in item_ids:
                    issue("error", "duplicate_item_id", f"行程项目编号重复：{item.item_id}。", day.day_index)
                item_ids.add(item.item_id)
                if previous is not None:
                    pair = (previous.item_id, item.item_id)
                    travel_minutes = route_durations.get(pair, item.travel_from_previous_minutes)
                    previous_end = datetime.combine(datetime.min, previous.start_time) + timedelta(
                        minutes=previous.duration_minutes + travel_minutes
                    )
                    current_start = datetime.combine(datetime.min, item.start_time)
                    if current_start < previous_end:
                        issue(
                            "error",
                            "time_conflict",
                            f"{previous.name}与{item.name}的时间或路程安排重叠。",
                            day.day_index,
                        )
                previous = item

        if abs(itinerary.total_cost - calculated_total) > money_epsilon:
            issue(
                "error",
                "total_cost_mismatch",
                f"行程总费用为 {itinerary.total_cost:.2f}，项目费用合计为 {calculated_total:.2f}。",
            )
        if budget is not None and calculated_total > budget + money_epsilon:
            issue(
                "error",
                "budget_exceeded",
                f"行程预计费用 {calculated_total:.2f} 超出预算 {budget:.2f}。",
            )

        return ValidationResult(
            passed=not any(item.severity == "error" for item in issues),
            issues=issues,
            checked_at=datetime.now(timezone.utc),
        )
