"""使用独立进程验证导入边界，不依赖测试收集顺序。"""

import os
import subprocess
import sys


def test_import_does_not_create_log_model_or_database(tmp_path):
    script = """
import socket
from unittest.mock import Mock
import httpx
import loguru
import sqlalchemy.ext.asyncio as db

def forbidden(*args, **kwargs):
    raise AssertionError('导入不得初始化资源')
socket.socket.connect = forbidden
socket.socket.connect_ex = forbidden
httpx.Client.__init__ = forbidden
httpx.AsyncClient.__init__ = forbidden
db.create_async_engine = forbidden
loguru.logger.add = forbidden
import app.core.config as config
config.get_settings = forbidden
import app.main
import app.schemas.api_schema
import app.models
assert app.main.app is not None
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                            env={**os.environ, "LOG_DIR": str(tmp_path / "logs")})
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "logs").exists()


def test_disabled_logging_removes_default_sink_and_preserves_external_sink():
    script = """
import asyncio
import os
os.environ['LOG_ENABLED'] = 'false'
from app.core.log import configure_logger, logger, shutdown_logger
messages = []
sink_id = logger.add(messages.append, format='{message}')
configure_logger(force=True)
logger.info('external-only-marker')
asyncio.run(shutdown_logger())
logger.info('external-after-shutdown')
assert len(messages) == 2
logger.remove(sink_id)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "external-only-marker" not in result.stderr
