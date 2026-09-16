"""高德 Web 服务异步客户端。

本模块只处理 HTTP 通信、接口参数和调用频率控制。POI 筛选、字段转换及
路线分段等业务规则由 :mod:`app.services.amap_service` 负责。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal, Self

import httpx

from app.core.logger import logger

AMAP_BASE_URL = "https://restapi.amap.com"
AMAP_REQUESTS_PER_SECOND = 3


class AmapClientError(RuntimeError):
    """高德接口发生网络、协议或业务错误。"""

    def __init__(self, message: str, *, info_code: str | None = None) -> None:
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
        if not api_key or not api_key.strip():
            raise ValueError("高德 API Key 不能为空")
        if timeout <= 0:
            raise ValueError("timeout 必须大于 0")
        if max_retries < 0:
            raise ValueError("max_retries 不能小于 0")

        self._api_key = api_key.strip()
        self._base_url = base_url.rstrip("/")
        self._max_retries = max_retries
        self._rate_limiter = _SlidingWindowRateLimiter(
            requests_per_second,
            1.0,
        )
        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
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
        """调用关键字搜索接口并返回高德原始响应。"""

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
        """调用地理编码接口并返回高德原始响应。"""

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

    async def calculate_route(
        self,
        *,
        origin: str,
        destination: str,
        mode: Literal["walking", "driving"] = "walking",
    ) -> dict[str, Any]:
        """调用步行或驾车路径规划接口并返回高德原始响应。

        ``origin`` 和 ``destination`` 均使用高德要求的 ``经度,纬度`` 格式。
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
            logger.debug("高德请求开始: 接口={}, 第 {} 次", path, attempt + 1)
            try:
                response = await self._http_client.get(
                    f"{self._base_url}{path}",
                    params=request_params,
                )
            except httpx.RequestError as exc:
                if attempt < self._max_retries:
                    logger.warning("高德网络请求重试: 接口={}, 第 {} 次", path, attempt + 1)
                    await asyncio.sleep(0.25 * (2**attempt))
                    continue
                logger.error("高德网络请求失败: 接口={}, 尝试次数={}", path, attempt + 1)
                # 不拼接原始异常，避免异常中的完整 URL 泄露 API Key。
                raise AmapClientError("请求高德地图失败，请检查网络连接") from exc

            if (
                response.status_code == 429 or response.status_code >= 500
            ) and attempt < self._max_retries:
                logger.warning(
                    "高德 HTTP 请求重试: 接口={}, 状态码={}, 第 {} 次",
                    path, response.status_code, attempt + 1,
                )
                await asyncio.sleep(0.25 * (2**attempt))
                continue

            if response.is_error:
                logger.error("高德 HTTP 请求失败: 接口={}, 状态码={}", path, response.status_code)
                raise AmapClientError(f"高德地图返回 HTTP {response.status_code}")

            try:
                payload = response.json()
            except ValueError as exc:
                logger.error("高德响应无法解析: 接口={}", path)
                raise AmapClientError("高德地图返回了无法解析的 JSON") from exc

            if not isinstance(payload, dict):
                logger.error("高德响应格式错误: 接口={}", path)
                raise AmapClientError("高德地图返回的数据格式不正确")

            if str(payload.get("status")) != "1":
                info = str(payload.get("info") or "未知错误")
                info_code = str(payload.get("infocode") or "") or None
                logger.error("高德业务请求失败: 接口={}, 错误码={}", path, info_code)
                suffix = f"（错误码 {info_code}）" if info_code else ""
                raise AmapClientError(
                    f"高德地图请求失败：{info}{suffix}",
                    info_code=info_code,
                )

            logger.debug("高德请求完成: 接口={}, 第 {} 次", path, attempt + 1)
            return payload

        # 循环中的成功和失败分支都会返回或抛错，仅用于满足静态检查。
        raise AmapClientError("请求高德地图失败")
