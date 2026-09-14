from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]

class Settings(BaseSettings):
    """应用配置，环境变量优先于 .env 文件中的同名配置。"""

    openai_api_key: str = Field(min_length=1)
    openai_base_url: str | None = None
    openai_model: str | None = None

    database_url: str | None = None

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
