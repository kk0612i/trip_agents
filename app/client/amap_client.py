"""高德 Web 服务异步客户端。

本模块只处理 HTTP 通信、接口参数和调用频率控制。POI 筛选、字段转换及
路线分段等业务规则由 :mod:`app.services.amap_service` 负责。
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Self

import httpx

from app.core.log import log_event

AMAP_BASE_URL = "https://restapi.amap.com"
AMAP_REQUESTS_PER_SECOND = 3


class AmapClientError(RuntimeError):
    """高德接口发生网络、协议或业务错误。"""

    def __init__(self, message: str, *, info_code: str | None = None) -> None:
        """保存可展示说明和可选高德错误码，不保留 HTTP 响应对象。"""
        super().__init__(message)
        self.info_code = info_code


class _SlidingWindowRateLimiter:
    """使用滑动窗口限制单个进程内发往高德的请求频率。"""

    def __init__(
        self,
        max_calls: int,
        period_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if max_calls <= 0:
            raise ValueError("max_calls 必须大于 0")
        if period_seconds <= 0:
            raise ValueError("period_seconds 必须大于 0")

        self._max_calls = max_calls
        self._period_seconds = period_seconds
        self._clock = clock
        self._sleep = sleep
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """等待至当前请求可以进入滑动窗口。"""

        while True:
            async with self._lock:
                now = self._clock()
                boundary = now - self._period_seconds

                # 移除窗口外的请求，仅保留最近一秒内已经发出的调用。
                while self._timestamps and self._timestamps[0] <= boundary:
                    self._timestamps.popleft()

                if len(self._timestamps) < self._max_calls:
                    self._timestamps.append(now)
                    return

                wait_seconds = self._timestamps[0] + self._period_seconds - now

            # 睡眠期间释放锁，让其他协程也能重新判断窗口状态。
            await self._sleep(max(wait_seconds, 0.0))


class AmapClient:
    """封装行程规划所需的高德 Web 服务接口。

    默认在同一客户端实例内限制每秒最多发出 3 个请求，与高德控制台中
    基础 LBS、搜索和路径规划接口的调用上限保持一致。
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = AMAP_BASE_URL,
        timeout: float = 10.0,
        requests_per_second: int = AMAP_REQUESTS_PER_SECOND,
        max_retries: int = 2,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """创建限流客户端，外部注入的 HTTP 客户端仍由调用方关闭。

        Args:
            api_key: 高德 Web 服务密钥，不写入日志和异常正文。
            base_url: 高德服务地址，可注入本地离线测试端点。
            timeout: 每次 HTTP 请求超时秒数，必须为正数。
            requests_per_second: 当前客户端实例每秒允许发出的请求数。
            max_retries: 可重试错误的额外重试次数，0 表示不重试。
            http_client: 可选外部 HTTP 客户端；None 时创建并拥有客户端。

        Raises:
            ValueError: 密钥为空，或超时、限流、重试配置不合法。
        """
        if not api_key or not api_key.strip():
            raise ValueError("高德 API Key 不能为空")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")

        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._timeout = timeout
        self._rate_limiter = _SlidingWindowRateLimiter(
            requests_per_second,
            1.0,
        )
        # 只释放本对象创建的连接池；注入客户端可由其他服务共享。
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> Self:
        """进入客户端作用域，不重复创建连接池。"""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """退出作用域时只释放自有 HTTP 资源。"""
        await self.aclose()

    async def aclose(self) -> None:
        """关闭由当前对象创建的 HTTP 客户端。"""

        if self._owns_http_client:
            await self._http_client.aclose()

    async def search_pois(
        self,
        *,
        keywords: str,
        city: str | None = None,
        page: int = 1,
        offset: int = 10,
    ) -> dict[str, Any]:
        """调用关键字搜索接口并返回高德原始响应。

        Args:
            keywords: 去除首尾空白后非空的搜索词。
            city: 可选城市名或代码，提供时限制搜索城市。
            page: 从 1 开始的结果页码。
            offset: 每页结果上限，范围为 1 到 25。

        Returns:
            通过通用协议校验的原始 JSON，字段规范化由服务负责。"""

        keywords = keywords.strip()
        if not keywords:
            raise ValueError("POI 搜索关键词不能为空")
        if page < 1:
            raise ValueError("page 必须大于等于 1")
        if not 1 <= offset <= 25:
            raise ValueError("offset 必须在 1 到 25 之间")

        return await self._request(
            "/v3/place/text",
            {
                "keywords": keywords,
                "city": city.strip() if city and city.strip() else None,
                "citylimit": "true" if city and city.strip() else None,
                "page": page,
                "offset": offset,
                "extensions": "base",
            },
        )

    async def geocode(
        self,
        *,
        address: str,
        city: str | None = None,
    ) -> dict[str, Any]:
        """调用地理编码接口并返回高德原始响应。

        Args:
            address: 非空地点地址或名称。
            city: 可选城市范围。

        Returns:
            含 geocodes 的高德原始 JSON，不在客户端筛选业务地点。"""

        address = address.strip()
        if not address:
            raise ValueError("地理编码地址不能为空")

        return await self._request(
            "/v3/geocode/geo",
            {
                "address": address,
                "city": city.strip() if city and city.strip() else None,
            },
        )

    async def get_poi_detail(self, *, poi_id: str) -> dict[str, Any]:
        """调用 POI 详情接口并返回高德原始响应。

        Args:
            poi_id: 非空高德 POI 编号。

        Returns:
            详情原始 JSON；扩展字段是否存在由上游决定。"""

        poi_id = poi_id.strip()
        if not poi_id:
            raise ValueError("POI ID 不能为空")

        return await self._request(
            "/v3/place/detail",
            {
                "id": poi_id,
                # 详情接口默认返回基础字段；扩展字段仅在高德实际提供时使用。
                "extensions": "all",
            },
        )

    async def get_weather(self, *, city: str) -> dict[str, Any]:
        """调用天气查询接口并返回高德原始响应。

        Args:
            city: 非空城市名或行政区代码。

        Returns:
            预报原始 JSON，不补齐上游未提供的日期。"""

        city = city.strip()
        if not city:
            raise ValueError("天气查询城市不能为空")

        return await self._request(
            "/v3/weather/weatherInfo",
            {
                "city": city,
                "extensions": "all",
            },
        )

    async def calculate_route(
        self,
        *,
        origin: str,
        destination: str,
        mode: Literal["walking", "driving"] = "walking",
    ) -> dict[str, Any]:
        """调用步行或驾车路径规划接口并返回高德原始响应。

        Args:
            origin: 起点坐标，格式为经度,纬度。
            destination: 终点坐标，格式为经度,纬度。
            mode: 步行或驾车方式，不自动改变调用者选择。

        Returns:
            路线原始 JSON，距离单位为米、时长单位为秒。
        """

        origin = origin.strip()
        destination = destination.strip()
        if not origin or not destination:
            raise ValueError("路线起点和终点坐标不能为空")
        if mode not in {"walking", "driving"}:
            raise ValueError(f"暂不支持的路线方式: {mode}")

        params: dict[str, Any] = {
            "origin": origin,
            "destination": destination,
        }
        if mode == "driving":
            # 驾车策略 0 表示速度优先，避免加入规避区域等额外业务条件。
            params["strategy"] = 0

        return await self._request(f"/v3/direction/{mode}", params)

    async def _request(
        self,
        path: str,
        params: Mapping[str, Any],
    ) -> dict[str, Any]:
        """发送请求、执行有限重试并校验高德通用响应字段。"""

        request_params = {
            key: value for key, value in params.items() if value is not None
        }
        request_params["key"] = self._api_key

        for attempt in range(self._max_retries + 1):
            await self._rate_limiter.acquire()
            started_at = time.perf_counter()
            fields = {"operation": path, "attempt": attempt + 1, "timeout_seconds": self._timeout}
            try:
                response = await self._http_client.get(
                    f"{self._base_url}{path}",
                    params=request_params,
                    timeout=self._timeout,
                )
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    log_event("amap_retry", level="WARNING", **fields, status="retrying", error_type=type(exc).__name__,
                              duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                log_event("amap_completed", level="ERROR", **fields, status="failed", error_type=type(exc).__name__,
                          duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                # 不拼接原始异常，避免异常中的完整 URL 泄露 API Key。
                raise AmapClientError("请求高德地图失败，请检查网络连接") from None

            if (
                response.status_code == 429 or response.status_code >= 500
            ) and attempt < self._max_retries:
                log_event("amap_retry", level="WARNING", **fields, status="retrying", status_code=response.status_code,
                          duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                await asyncio.sleep(0.25 * (2**attempt))
                continue

            if response.is_error:
                log_event("amap_completed", level="ERROR", **fields, status="failed", status_code=response.status_code,
                          error_type="HTTPStatusError", duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                raise AmapClientError(f"高德地图返回 HTTP {response.status_code}")

            try:
                payload = response.json()
            except ValueError as exc:
                log_event("amap_completed", level="ERROR", **fields, status="failed", error_type=type(exc).__name__,
                          duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                raise AmapClientError("高德地图返回了无法解析的 JSON") from None

            if not isinstance(payload, dict):
                log_event("amap_completed", level="ERROR", **fields, status="failed", error_type="InvalidPayload",
                          duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                raise AmapClientError("高德地图返回的数据格式不正确")

            if str(payload.get("status")) != "1":
                # 上游错误文本不可信，可能包含请求 URL 或密钥；只保留标准数字错误码。
                raw_code = str(payload.get("infocode") or "")
                info_code = raw_code if re.fullmatch(r"\d{5,6}", raw_code) else None
                log_event("amap_completed", level="ERROR", **fields, status="failed", error_type="AmapBusinessError",
                          info_code=info_code, duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
                suffix = f"（错误码 {info_code}）" if info_code else ""
                raise AmapClientError(
                    f"高德地图请求失败{suffix}",
                    info_code=info_code,
                )

            log_event("amap_completed", **fields, status="completed", status_code=response.status_code,
                      duration_ms=round((time.perf_counter() - started_at) * 1000, 2))
            return payload

        # 循环中的成功和失败分支都会返回或抛错，仅用于满足静态检查。
        raise AmapClientError("请求高德地图失败")
