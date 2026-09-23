"""内部结果提交工具；直接终止规划循环，不进入公共工具额度账本。"""

from typing import Any

from langchain_core.tools import StructuredTool

from app.schemas.planner_schema import _PlannerResult


def _submit_plan(**kwargs: Any) -> tuple[str, dict[str, Any]]:
    """验证最终结果并同时返回模型可读内容与结构化数据。

    Args:
        **kwargs: 第三方工具框架传入的原始结果字段。

    Returns:
        JSON 内容及对应结果字典；仅做内部提交，不执行业务查询。
    """
    result = _PlannerResult.model_validate(kwargs)
    return result.model_dump_json(), result.model_dump(mode="json")


SUBMIT_PLAN: StructuredTool = StructuredTool.from_function(
    func=_submit_plan, name="_PlannerResult", description=_PlannerResult.__doc__,
    args_schema=_PlannerResult, return_direct=True, response_format="content_and_artifact",
)
