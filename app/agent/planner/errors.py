"""Planner 可公开展示的业务校验异常。"""


class PlanningError(ValueError):
    """标记安全的业务拒绝原因，不携带模型或外部服务原始响应。"""
