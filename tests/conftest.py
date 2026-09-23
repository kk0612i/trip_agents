"""默认测试禁止真实网络；外部接口使用 MockTransport 或 fake。"""

import socket
import ipaddress

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.resources import AppResources


@pytest.fixture
def offline_db_resources():
    """提供未绑定引擎的真实会话工厂，供 HTTP 占位接口验证依赖生命周期。

    Returns:
        使用真实 AsyncSession 的资源容器；执行 SQL 会因未绑定引擎而失败。
    """
    return AppResources(session_factory=async_sessionmaker())


@pytest.fixture(autouse=True)
def block_network_for_unit_tests(request, monkeypatch):
    if request.node.get_closest_marker("integration"):
        return

    def guard(original):
        def connect(sock, address):
            # Windows asyncio 用本机 socketpair 创建唤醒管道。
            if isinstance(address, tuple):
                try:
                    if ipaddress.ip_address(address[0]).is_loopback:
                        return original(sock, address)
                except ValueError:
                    pass
            raise AssertionError("默认测试禁止真实网络，请使用 MockTransport 或 Fake")
        return connect

    monkeypatch.setattr(socket.socket, "connect", guard(socket.socket.connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guard(socket.socket.connect_ex))
