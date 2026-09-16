import httpx
import pytest

from app.client.amap_client import (
    AmapClient,
    AmapClientError,
    _SlidingWindowRateLimiter,
)


@pytest.mark.asyncio
async def test_search_pois_sends_expected_parameters():
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"status": "1", "pois": []})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AmapClient("secret-key", http_client=http_client)

    response = await client.search_pois(keywords="景点", city="长沙", offset=8)

    assert response == {"status": "1", "pois": []}
    assert captured_request is not None
    assert captured_request.url.path == "/v3/place/text"
    assert captured_request.url.params["key"] == "secret-key"
    assert captured_request.url.params["keywords"] == "景点"
    assert captured_request.url.params["city"] == "长沙"
    assert captured_request.url.params["citylimit"] == "true"
    assert captured_request.url.params["offset"] == "8"
    await http_client.aclose()


@pytest.mark.asyncio
async def test_amap_business_error_keeps_info_code_without_exposing_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"},
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AmapClient("secret-key", http_client=http_client)

    with pytest.raises(AmapClientError) as exc_info:
        await client.geocode(address="长沙市岳麓山")

    assert exc_info.value.info_code == "10001"
    assert "secret-key" not in str(exc_info.value)
    await http_client.aclose()


@pytest.mark.asyncio
async def test_rate_limiter_waits_after_three_calls_in_one_second():
    now = 0.0

    def clock() -> float:
        return now

    async def sleep(seconds: float) -> None:
        nonlocal now
        now += seconds

    limiter = _SlidingWindowRateLimiter(
        3,
        1.0,
        clock=clock,
        sleep=sleep,
    )

    for _ in range(4):
        await limiter.acquire()

    assert now == pytest.approx(1.0)
