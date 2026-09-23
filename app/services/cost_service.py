"""确定性费用规则，不依赖工具 Runtime、模型或网络资源。"""

from decimal import Decimal
from typing import TypedDict

from app.schemas.tool_schema import CostItem
from app.schemas.trip_schema import Itinerary


class CostSummary(TypedDict):
    """费用汇总；金额按 currency 指定币种计，None 表示未知。"""

    known_total: float
    breakdown: dict[str, float | None]
    unknown_items: list[dict[str, object]]
    currency: str
    budget: float | None
    within_budget: bool | None
    assumptions: list[str]


def summarize_itinerary_cost(
    items: list[CostItem],
    budget: float | None,
    currency: str,
    draft: Itinerary | None = None,
) -> CostSummary:
    """汇总已知金额并保留未知项，不自动推断人数倍率或免费项目。

    Args:
        items: 已校验的整项总价；金额为 None 表示尚未核验。
        budget: 已由调用方确定的预算，None 表示没有预算。
        currency: 输出币种；本函数不进行汇率转换。
        draft: 没有显式费用项时，从草稿读取门票估价；不修改草稿。

    Returns:
        已知合计、分类金额、未知项与预算判断；存在未知费用时不判断预算。
    """
    costs = list(items)
    assumptions = ["金额按整项总价汇总，不自动推断单价、人数倍率或免费项目"]
    if not costs:
        if draft is not None:
            costs.extend(CostItem(category="tickets", amount=item.estimated_cost,
                                  description=item.name)
                         for day in draft.days for item in day.items)
    # 未提供的核心费用分类记为未知；不适用的分类也须由调用者明确填写 0。
    present = {item.category for item in costs}
    costs.extend(CostItem(category=category, description=f"未提供{label}费用")
                 for category, label in (("transportation", "交通"), ("tickets", "门票"),
                                         ("meals", "餐饮"), ("accommodation", "住宿"))
                 if category not in present)
    totals = {category: Decimal("0") for category in
              ("transportation", "tickets", "meals", "accommodation", "other")}
    known_categories: set[str] = set()
    unknown_items = []
    for item in costs:
        if item.amount is None:
            unknown_items.append(item.model_dump(mode="json"))
        else:
            known_categories.add(item.category)
            totals[item.category] += Decimal(str(item.amount))
    known_total = sum(totals.values(), Decimal("0"))
    return {
        "known_total": float(known_total),
        # 没有已知金额的分类返回 null；已明确免费才返回 0。
        "breakdown": {key: float(value) if key in known_categories else None
                      for key, value in totals.items()},
        "unknown_items": unknown_items,
        "currency": currency,
        "budget": budget,
        "within_budget": (None if unknown_items or budget is None
                          else known_total <= Decimal(str(budget))),
        "assumptions": assumptions,
    }
