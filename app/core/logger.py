"""Loguru 统一日志配置。

业务模块只需：

    from app.core.logger import logger

模块导入时会完成一次配置。日志文件采用项目根目录 ``logs/app_YYYYMMDD.log``
命名，具体开关和保留策略由 ``.env`` 中的 ``LOG_*`` 变量控制。
"""

from __future__ import annotations

import os
import sys
import threading
import functools
import inspect
import time
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar, cast

from loguru import logger as _logger

from app.core.config import PROJECT_DIR, get_settings

try:
    from dotenv import load_dotenv

    # 让日志在业务必填配置缺失时也能读取 LOG_*，保持“导入即用”。
    load_dotenv(PROJECT_DIR / ".env", override=False)
except ImportError:  # pragma: no cover - pydantic-settings 通常会带上该依赖
    pass

_CONFIG_LOCK = threading.Lock()
_CONFIGURED = False
F = TypeVar("F", bound=Callable[..., Any])

# 统一格式中使用调用方的 name/function/line。直接使用 Loguru logger 时，
# Loguru 会自动跳过自身内部帧；下面的辅助函数通过 depth 继续跳过辅助层。
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:<8}</level> | "
    "{process.id}:{thread.id} | "
    "{name}:{function}:{line} | {message}"
)


def configure_logger(*, force: bool = False) -> None:
    """按当前配置安装 Loguru sinks。

    ``force=True`` 主要用于测试或运行时重新加载环境变量；正常业务代码无需调用。
    ``logger.remove()`` 保证重复导入/重载不会产生重复日志。
    """

    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    with _CONFIG_LOCK:
        if _CONFIGURED and not force:
            return

        if force:
            get_settings.cache_clear()
        try:
            settings = get_settings()
        except Exception:
            # 日志模块不应因为尚未配置 LLM/数据库而无法导入；应用启动时
            # 仍由 get_settings() 对业务配置执行完整校验。
            settings = _fallback_settings()
        _logger.remove()

        if not settings.log_enabled:
            _CONFIGURED = True
            return

        level = settings.log_level.upper().strip() or "INFO"
        # 目录由配置提供；默认值就是项目根目录 / logs。
        log_dir = Path(settings.log_dir)
        if not log_dir.is_absolute():
            log_dir = PROJECT_DIR / log_dir

        sink_options: dict[str, Any] = {
            "format": LOG_FORMAT,
            "level": level,
            "enqueue": settings.log_enqueue,
            "backtrace": False,
            "diagnose": False,
            "catch": True,
        }

        if settings.log_console_enabled:
            _logger.add(sys.stderr, **sink_options)

        if settings.log_file_enabled:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_options = {
                **sink_options,
                "rotation": settings.log_rotation,
                "retention": settings.log_retention,
                "encoding": settings.log_encoding or "utf-8",
            }
            # {time:YYYYMMDD} 生成 app_20260914.log；rotation=00:00 切换到新日期。
            _logger.add(log_dir / "app_{time:YYYYMMDD}.log", **file_options)

        _CONFIGURED = True


def _fallback_settings():
    """在业务配置不完整时读取日志所需的最小配置。"""

    from types import SimpleNamespace

    def as_bool(name: str, default: bool) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    return SimpleNamespace(
        log_enabled=as_bool("LOG_ENABLED", True),
        log_console_enabled=as_bool("LOG_CONSOLE_ENABLED", True),
        log_file_enabled=as_bool("LOG_FILE_ENABLED", True),
        log_level=os.getenv("LOG_LEVEL", "INFO"),
        log_retention=os.getenv("LOG_RETENTION", "14 days"),
        log_rotation=os.getenv("LOG_ROTATION", "00:00"),
        log_dir=Path(os.getenv("LOG_DIR", str(PROJECT_DIR / "logs"))),
        log_encoding=os.getenv("LOG_ENCODING", "utf-8"),
        log_enqueue=as_bool("LOG_ENQUEUE", True),
    )


def get_logger():
    """返回已配置的共享 logger，便于依赖注入和测试。"""

    return _logger


def log(level: str, message: str, *args: Any, **kwargs: Any) -> None:
    """辅助入口，自动把调用位置定位到业务代码。"""

    _logger.opt(depth=1).log(level, message, *args, **kwargs)


def debug(message: str, *args: Any, **kwargs: Any) -> None:
    _logger.opt(depth=1).debug(message, *args, **kwargs)


def info(message: str, *args: Any, **kwargs: Any) -> None:
    _logger.opt(depth=1).info(message, *args, **kwargs)


def warning(message: str, *args: Any, **kwargs: Any) -> None:
    _logger.opt(depth=1).warning(message, *args, **kwargs)


def error(message: str, *args: Any, **kwargs: Any) -> None:
    _logger.opt(depth=1).error(message, *args, **kwargs)


def exception(message: str, *args: Any, **kwargs: Any) -> None:
    _logger.opt(depth=1).exception(message, *args, **kwargs)


def node_log(func: F) -> F:
    """记录 LangGraph 节点的开始、结束耗时和异常。

    同时支持同步/异步节点，并保留原函数签名。异常会先记录完整堆栈，
    然后继续抛出给 LangGraph 或上层调用方处理，不改变原有错误语义。
    """

    node_name = func.__name__

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            logger.bind(node=node_name).info("节点开始: {}", node_name)
            try:
                result = await func(*args, **kwargs)
            except Exception:
                elapsed = time.perf_counter() - started_at
                logger.bind(node=node_name).exception(
                    "节点异常: {}, 耗时 {:.3f}s", node_name, elapsed
                )
                raise
            elapsed = time.perf_counter() - started_at
            logger.bind(node=node_name).info(
                "节点结束: {}, 耗时 {:.3f}s", node_name, elapsed
            )
            return result

        return cast(F, async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        logger.bind(node=node_name).info("节点开始: {}", node_name)
        try:
            result = func(*args, **kwargs)
        except Exception:
            elapsed = time.perf_counter() - started_at
            logger.bind(node=node_name).exception(
                "节点异常: {}, 耗时 {:.3f}s", node_name, elapsed
            )
            raise
        elapsed = time.perf_counter() - started_at
        logger.bind(node=node_name).info("节点结束: {}, 耗时 {:.3f}s", node_name, elapsed)
        return result

    return cast(F, sync_wrapper)


# 导入即用：所有模块直接导入 logger 即可，不要求在 main 中额外初始化。
configure_logger()

# 对外暴露的 logger 是 Loguru 原生对象，保留 bind/opt/exception 等完整 API。
logger = _logger

__all__ = [
    "logger",
    "configure_logger",
    "get_logger",
    "log",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "node_log",
]
