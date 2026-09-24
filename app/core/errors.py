"""跨入口共用的业务异常，不依赖 HTTP 状态码或框架对象。"""


class CapabilityUnavailableError(RuntimeError):
    """能力框架已存在，但尚不能执行业务操作。"""

    # API 层可使用此稳定错误码进行映射；异常本身不决定 HTTP 响应。
    code: str = "CAPABILITY_UNAVAILABLE"

    def __init__(self, capability: str) -> None:
        """记录尚未实现的能力。

        Args:
            capability: 可安全展示给用户的能力名称，不包含原始请求或密钥。
        """
        # 未实现能力的业务名称，供入口日志及错误响应使用。
        self.capability = capability
        super().__init__(f"{capability}尚未实现")

class BusinessError(Exception):
    """预期内的业务失败。"""


class EmailAlreadyRegisteredError(BusinessError):
    pass


class AccountNotFoundError(BusinessError):
    pass


class InvalidPasswordError(BusinessError):
    pass


class AuthenticationFailedError(BusinessError):
    pass