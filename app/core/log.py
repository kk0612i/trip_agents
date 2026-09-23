"""Loguru 统一日志配置。

业务模块只需：

    from app.core.log import logger

应用入口在启动时显式配置，模块导入不创建文件。日志文件采用项目根目录 ``logs/app_YYYYMMDD.log``
命名，具体开关和保留策略由 ``.env`` 中的 ``LOG_*`` 变量控制。
"""

from __future__ import annotations

import os
import sys
import threading
import functools
import inspect
import time
import re
from pathlib import Path
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, TypeVar, cast
from types import SimpleNamespace

if TYPE_CHECKING:
    from loguru import Logger

from loguru import logger as _logger

from app.core.config import PROJECT_DIR, get_settings

_CONFIG_LOCK = threading.Lock()
_CONFIGURED = False
_OWNED_SINKS: list[int] = []
_DEFAULT_SINK_REMOVED = False
F = TypeVar("F", bound=Callable[..., Any])

# 统一格式中使用调用方的 name/function/line。直接使用 Loguru logger 时，
# Loguru 会自动跳过自身内部帧；下面的辅助函数通过 depth 继续跳过辅助层。
LOG_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
    "<level>{level:<8}</level> | "
    "{process.id}:{thread.id} | request_id={extra[request_id]} | run_id={extra[run_id]} | "
    "{name}:{function}:{line} | {message}"
)


def configure_logger(*, force: bool = False) -> None:
    """按当前配置安装 Loguru sinks。

    ``force=True`` 主要用于测试或运行时重新加载环境变量；正常业务代码无需调用。
    仅替换本模块登记的 sinks，不移除调用方注入的日志接收器。
    """

    global _CONFIGURED, _DEFAULT_SINK_REMOVED
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
        # Loguru 自带 stderr sink 的编号固定为 0；只接管这一默认项，
        # 保留宿主和测试额外登记的 sinks，避免重复输出及禁用日志仍输出。
        if not _DEFAULT_SINK_REMOVED:
            try:
                _logger.remove(0)
            except ValueError:
                pass  # 宿主可能已移除库默认输出。
            _DEFAULT_SINK_REMOVED = True
        _remove_owned_sinks()
        _logger.configure(extra={"run_id": "-", "request_id": "-"})

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
            _OWNED_SINKS.append(_logger.add(sys.stderr, **sink_options))

        if settings.log_file_enabled:
            log_dir.mkdir(parents=True, exist_ok=True)
            file_options = {
                **sink_options,
                "rotation": settings.log_rotation,
                "retention": settings.log_retention,
                "encoding": settings.log_encoding or "utf-8",
            }
            # {time:YYYYMMDD} 生成 app_20260914.log；rotation=00:00 切换到新日期。
            _OWNED_SINKS.append(_logger.add(log_dir / "app_{time:YYYYMMDD}.log", **file_options))

        _CONFIGURED = True


def _fallback_settings() -> SimpleNamespace:
    """在业务配置不完整时读取日志所需的最小配置。"""

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


def get_logger() -> Logger:
    """返回共享 logger；配置和释放由入口负责。"""

    return _logger


def safe_log_identifier(value: object) -> str:
    """返回限长且不含控制字符的技术标识；不合法内容统一隐藏。

    Args:
        value: 运行、工具调用或模型的技术标识，不得传入用户正文或凭据。

    Returns:
        最多 128 个安全 ASCII 字符；缺失或格式不合法时为占位值。
    """
    return value if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,128}", value) else "-"


def log_event(event: str, *, level: str = "INFO", **fields: str | int | float | bool | None) -> None:
    """记录稳定事件及结构化字段，同时保留便于人工阅读的键值摘要。

    Args:
        event: 代码中固定的事件名称。
        level: Loguru 日志级别。
        fields: 调用方筛选后的技术元数据；禁止传入请求体、凭据及异常原文。
    """
    # 字段作为 extra 供采集器读取；当前文本文件也保留同一组字段。
    summary = " ".join(f"{key}={value}" for key, value in fields.items() if value is not None)
    _logger.bind(event=event, **fields).opt(depth=1).log(level, "event={} {}", event, summary)


def log(level: str, message: str, *args: Any, **kwargs: Any) -> None:
    """辅助入口，自动把调用位置定位到业务代码。"""

    _logger.opt(depth=1).log(level, message, *args, **kwargs)


def debug(message: str, *args: Any, **kwargs: Any) -> None:
    """记录调试摘要并保留业务调用位置。"""
    _logger.opt(depth=1).debug(message, *args, **kwargs)


def info(message: str, *args: Any, **kwargs: Any) -> None:
    """记录正常业务进度并保留调用位置。"""
    _logger.opt(depth=1).info(message, *args, **kwargs)


def warning(message: str, *args: Any, **kwargs: Any) -> None:
    """记录可预期异常摘要，不附加原始响应。"""
    _logger.opt(depth=1).warning(message, *args, **kwargs)


def error(message: str, *args: Any, **kwargs: Any) -> None:
    """记录业务失败摘要，避免敏感请求内容。"""
    _logger.opt(depth=1).error(message, *args, **kwargs)


def exception(message: str, *args: Any, **kwargs: Any) -> None:
    """在异常边界记录堆栈；调用方负责确认内容已脱敏。"""
    _logger.opt(depth=1).exception(message, *args, **kwargs)


def node_log(func: F) -> F:
    """记录 LangGraph 节点的开始、结束耗时和异常。

    同时支持同步/异步节点，并保留原函数签名。只记录异常类型，避免上游
    异常链中的请求内容或密钥进入日志；异常仍交给上层处理。
    """

    node_name = func.__name__

    def run_id_from_call(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        bound = inspect.signature(func).bind_partial(*args, **kwargs)
        state = bound.arguments.get("state")
        if isinstance(state, Mapping) and state.get("run_id"):
            return safe_log_identifier(state["run_id"])
        return "-"

    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            started_at = time.perf_counter()
            with logger.contextualize(run_id=run_id_from_call(args, kwargs)):
                log_event("node_started", level="DEBUG", node=node_name, status="running")
                try:
                    result = await func(*args, **kwargs)
                except Exception as exc:
                    elapsed = time.perf_counter() - started_at
                    log_event("node_failed", level="ERROR", node=node_name, status="failed",
                              error_type=type(exc).__name__, duration_ms=round(elapsed * 1000, 2))
                    raise
                elapsed = time.perf_counter() - started_at
                log_event("node_completed", level="DEBUG", node=node_name, status="completed", duration_ms=round(elapsed * 1000, 2))
                return result

        return cast(F, async_wrapper)

    @functools.wraps(func)
    def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
        started_at = time.perf_counter()
        with logger.contextualize(run_id=run_id_from_call(args, kwargs)):
            log_event("node_started", level="DEBUG", node=node_name, status="running")
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                elapsed = time.perf_counter() - started_at
                log_event("node_failed", level="ERROR", node=node_name, status="failed",
                          error_type=type(exc).__name__, duration_ms=round(elapsed * 1000, 2))
                raise
            elapsed = time.perf_counter() - started_at
            log_event("node_completed", level="DEBUG", node=node_name, status="completed", duration_ms=round(elapsed * 1000, 2))
            return result

    return cast(F, sync_wrapper)


# 业务模块导入原生对象不会创建文件或连接。

# 对外暴露的 logger 是 Loguru 原生对象，保留 bind/opt/exception 等完整 API。
logger = _logger

__all__ = [
    "logger",
    "configure_logger",
    "shutdown_logger",
    "get_logger",
    "log",
    "debug",
    "info",
    "warning",
    "error",
    "exception",
    "node_log",
    "log_event",
    "safe_log_identifier",
]


def _remove_owned_sinks() -> None:
    """移除本模块拥有的 sink，保留测试或宿主注入的接收器。"""
    while _OWNED_SINKS:
        sink_id = _OWNED_SINKS.pop()
        try:
            _logger.remove(sink_id)
        except ValueError:
            # 宿主可能已主动移除该接收器。
            continue


async def shutdown_logger() -> None:
    """入口退出时排空日志队列，仅释放本模块登记的日志文件。"""
    global _CONFIGURED
    try:
        await _logger.complete()
    finally:
        with _CONFIG_LOCK:
            _remove_owned_sinks()
            _CONFIGURED = False
