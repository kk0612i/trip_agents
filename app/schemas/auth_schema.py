"""认证 DTO 入口，复用正式 API 契约，避免维护两套认证校验规则。"""

from app.schemas.api_schema import AuthResponse, Credentials, UserView

# 本模块对外导出的正式认证模型；复用同一契约以避免输入规则和响应字段分叉。
__all__ = ["AuthResponse", "Credentials", "UserView"]
