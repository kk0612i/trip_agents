import httpx
import pytest
import traceback

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
@pytest.mark.parametrize(
    ("call", "path", "expected"),
    [
        (
            lambda client: client.geocode(address="岳麓山", city="长沙"),
            "/v3/geocode/geo",
            {"address": "岳麓山", "city": "长沙"},
        ),
        (
            lambda client: client.calculate_route(origin="112,28", destination="113,29"),
            "/v3/direction/walking",
            {"origin": "112,28", "destination": "113,29"},
        ),
        (
            lambda client: client.calculate_route(origin="112,28", destination="113,29", mode="driving"),
            "/v3/direction/driving",
            {"origin": "112,28", "destination": "113,29", "strategy": "0"},
        ),
        (
            lambda client: client.get_poi_detail(poi_id="B001"),
            "/v3/place/detail",
            {"id": "B001", "extensions": "all"},
        ),
        (
            lambda client: client.get_weather(city="长沙"),
            "/v3/weather/weatherInfo",
            {"city": "长沙", "extensions": "all"},
        ),
    ],
)
async def test_api_methods_send_expected_parameters(call, path, expected):
    captured_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"status": "1"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = AmapClient("secret-key", http_client=http_client)

    await call(client)

    assert captured_request is not None
    assert captured_request.url.path == path
    for key, value in expected.items():
        assert captured_request.url.params[key] == value
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


@pytest.mark.asyncio
@pytest.mark.parametrize("error_kind", ["network", "http", "json", "business"])
async def test_provider_failures_are_redacted_including_traceback(error_kind):
    async def handler(request):
        if error_kind == "network":
            raise httpx.ConnectError(f"secret-key {request.url}", request=request)
        if error_kind == "http":
            return httpx.Response(403, text="secret-key")
        if error_kind == "json":
            return httpx.Response(200, text="secret-key malformed json")
        return httpx.Response(200, json={"status": "0", "info": "secret-key", "infocode": "secret-key"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AmapClient("secret-key", http_client=http_client, max_retries=0)
        with pytest.raises(AmapClientError) as exc_info:
            await client.geocode(address="长沙")
        assert "secret-key" not in str(exc_info.value)
        assert "secret-key" not in "".join(traceback.format_exception(exc_info.value))
        assert exc_info.value.info_code is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", 429, 503])
async def test_retries_are_finite_and_every_attempt_is_rate_limited(monkeypatch, failure):
    from unittest.mock import AsyncMock
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if failure == "network":
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(failure)

    sleep = AsyncMock()
    monkeypatch.setattr("app.client.amap_client.asyncio.sleep", sleep)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AmapClient("secret-key", http_client=http_client, max_retries=2)
        limiter = AsyncMock()
        client._rate_limiter = limiter
        with pytest.raises(AmapClientError):
            await client.geocode(address="长沙")
    assert calls == 3
    assert limiter.acquire.await_count == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_transient_failure_can_recover(monkeypatch):
    from unittest.mock import AsyncMock
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503 if calls == 1 else 200, json={"status": "1", "pois": []})

    monkeypatch.setattr("app.client.amap_client.asyncio.sleep", AsyncMock())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = AmapClient("secret-key", http_client=http_client)
        assert await client.search_pois(keywords="景点") == {"status": "1", "pois": []}
    assert calls == 2
