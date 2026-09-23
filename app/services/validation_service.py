"""行程确定性校验能力的应用层接口。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.trip_schema import Itinerary, RouteInfo, ValidationIssue, ValidationResult


class ValidationService:
    """使用 Python 规则检查时间、预算和路线约束。"""

    def validate(
        self,
        itinerary: Itinerary,
        routes: list[RouteInfo],
        budget: float | None,
    ) -> ValidationResult:
        """在保存前检查行程金额、时间、路线及预算的一致性。

        Args:
            itinerary: 待检查的内部行程快照，金额单位人民币元。
            routes: 与草稿对应的路线证据；距离单位公里、时长单位分钟。
            budget: 全体出行人的整份行程预算，单位元；None 表示未提供上限。

        Returns:
            确定性校验结果；未知费用或约束违反时 passed 为 False。
        """
        issues: list[ValidationIssue] = []
        # 金额比较容差，单位人民币元；沿用既有浮点校验边界。
        money_epsilon = 0.01

        def issue(severity: str, code: str, message: str, day_index: int | None = None) -> None:
            """追加结构化校验问题；day_index 为 None 时影响整份行程。"""
            issues.append(
                ValidationIssue(
                    severity=severity, code=code, message=message, day_index=day_index
                )
            )

        # 工具和 POI 的未知费用以 None 传递；不能在保存校验中把它当作免费。
        if any(item.estimated_cost is None for day in itinerary.days for item in day.items):
            issue("error", "unknown_cost", "行程存在未知费用，尚不能确认完整总额或预算。")
            return ValidationResult(passed=False, issues=issues, checked_at=datetime.now(timezone.utc))

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
