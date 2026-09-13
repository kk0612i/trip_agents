from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，环境变量优先于 .env 文件中的同名配置。"""

    openai_api_key: str = Field(min_length=1)
    openai_base_url: str | None = None
    openai_model: str | None = None

    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8000, ge=1, le=65535)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    """返回缓存的应用配置实例。"""

    return Settings()
