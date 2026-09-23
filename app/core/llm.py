"""创建由调用方管理的 LLM 对象；应用容器负责复用。"""

import httpx

from langchain_openai import ChatOpenAI

from app.core.config import get_settings


def get_llm(
    *, http_client: httpx.Client | None = None,
    http_async_client: httpx.AsyncClient | None = None,
) -> ChatOpenAI:
    """创建模型；传入的 HTTP 客户端由调用方负责关闭。

    独立脚本未传客户端时，由脚本负责模型底层客户端的资源生命周期。

    Args:
        http_client: 调用方拥有的同步 HTTP 客户端；None 时由模型 SDK 创建。
        http_async_client: 调用方拥有的异步 HTTP 客户端；None 时由模型 SDK 创建。

    Returns:
        按当前模型配置构建的聊天模型；调用方管理其生命周期。
    """
    settings = get_settings()
    if not settings.openai_model or not settings.openai_model.strip():
        raise ValueError("请在 .env 中配置 OPENAI_MODEL")

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        model=settings.openai_model,
        timeout=60,
        max_retries=2,
        http_client=http_client,
        http_async_client=http_async_client,
    )
