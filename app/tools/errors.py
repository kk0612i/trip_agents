"""工具注册与执行的脱敏异常。"""


class RegistryError(RuntimeError):
    """未知目标、权限不足或不合法的工具声明。"""


class ToolExecutionError(RuntimeError):
    """工具执行失败；只携带脱敏后的说明。"""
