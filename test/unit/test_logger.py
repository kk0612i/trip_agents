import asyncio

import httpx
import pytest

from app.agents.nodes.context import load_context
from app.client.amap_client import AmapClient, AmapClientError
from app.core.logger import logger, node_log


@pytest.mark.asyncio
async def test_node_logs_keep_concurrent_run_ids_separate():
    messages = []
    sink_id = logger.add(messages.append, format="{extra[run_id]}|{message}", enqueue=False)

    @node_log
    async def sample_node(state):
        await asyncio.sleep(0)
        logger.info("内部调用完成")

    try:
        await asyncio.gather(
            sample_node({"run_id": "run-a"}),
            sample_node({"run_id": "run-b"}),
        )
    finally:
        logger.remove(sink_id)

    lines = [str(message).strip() for message in messages]
    assert lines.count("run-a|内部调用完成") == 1
    assert lines.count("run-b|内部调用完成") == 1
    assert sum(line.startswith("run-a|") for line in lines) == 3
    assert sum(line.startswith("run-b|") for line in lines) == 3


@pytest.mark.asyncio
async def test_missing_run_id_is_created_and_error_log_omits_exception_text():
    messages = []
    sink_id = logger.add(messages.append, format="{extra[run_id]}|{message}", enqueue=False)

    @node_log
    def failing_node(state):
        raise RuntimeError("secret-key")

    try:
        result = await load_context({"trip_id": None}, None)
        with pytest.raises(RuntimeError, match="secret-key"):
            failing_node({"run_id": result["run_id"]})
    finally:
        logger.remove(sink_id)

    assert len(result["run_id"]) == 32
    lines = [str(message) for message in messages]
    assert any(result["run_id"] in line for line in lines)
    assert any("节点异常: failing_node, 类型=RuntimeError" in line for line in lines)
    assert all("secret-key" not in line for line in lines)


@pytest.mark.asyncio
async def test_amap_network_error_log_omits_api_key():
    messages = []
    sink_id = logger.add(messages.append, format="{message}", enqueue=False)

    async def handler(request):
        raise httpx.ConnectError("secret-key", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AmapClient("secret-key", max_retries=0, http_client=http_client)
        try:
            with pytest.raises(AmapClientError):
                await client.search_pois(keywords="景点")
        finally:
            logger.remove(sink_id)

    lines = [str(message) for message in messages]
    assert any("高德网络请求失败" in line for line in lines)
    assert all("secret-key" not in line for line in lines)
