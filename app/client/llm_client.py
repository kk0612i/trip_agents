"""创建并复用应用的 LLM 对象。"""

from functools import lru_cache

from langchain_openai import ChatOpenAI

from app.config import get_settings


@lru_cache(maxsize=1)
def get_llm() -> ChatOpenAI:
    """首次调用时创建模型；业务服务通过构造参数接收该对象。"""
    settings = get_settings()
    if not settings.openai_model or not settings.openai_model.strip():
        raise ValueError("请在 .env 中配置 OPENAI_MODEL")

    return ChatOpenAI(
        api_key=settings.openai_api_key,
        base_url=settings.openai_base_url or None,
        model=settings.openai_model,
        timeout=60,
        max_retries=2,
    )
