"""集中声明应用配置；只有显式调用 get_settings 时才读取环境及 .env。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    """应用配置，环境变量优先于 .env 文件中的同名配置。"""

    # 模型服务密钥，必填且不能出现在技术日志中。
    openai_api_key: str = Field(min_length=1)
    # 兼容 API 基地址；None 时沿用 SDK 默认服务。
    openai_base_url: str | None = None
    # 模型名称；None 表示未配置，首次创建模型时明确拒绝。
    openai_model: str | None = None

    # MySQL 异步连接串；None 表示未配置，禁止记录完整内容。
    database_url: str | None = None

    # 供调用方启动配置使用；Uvicorn CLI 仍以显式参数为准。
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)

    # 日志配置：环境变量名称分别对应 LOG_*，例如 LOG_LEVEL=DEBUG。
    log_enabled: bool = True
    log_console_enabled: bool = True
    log_file_enabled: bool = True
    log_level: str = "INFO"
    log_retention: str = "14 days"
    log_rotation: str = "00:00"
    log_dir: Path = PROJECT_DIR / "logs"
    log_encoding: str = "utf-8"
    log_enqueue: bool = True
    # 高德服务密钥；业务服务通过客户端注入取得能力。
    amap_api_key: str = Field(min_length=1)
    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """返回缓存的应用配置实例。"""

    return Settings()
