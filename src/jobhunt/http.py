import asyncio
import time
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any
from urllib.parse import urlsplit

import httpx

USER_AGENT = "jobhunt/0.1"
RETRIES = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class FetchError(Exception):
    def __init__(self, url: str, status: int | None, reason: str) -> None:
        super().__init__(f"{status} {reason} ({url})")
        self.url, self.status, self.reason = url, status, reason


class PoliteClient:
    def __init__(
        self,
        *,
        min_interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout: float = 20.0,
    ) -> None:
        self._min_interval = min_interval
        self._clock = clock
        self._sleep = sleep
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        )
        self._locks: dict[str, asyncio.Lock] = {}
        self._last: dict[str, float] = {}

    async def __aenter__(self) -> PoliteClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def get_json(self, url: str, **params: Any) -> Any:
        return (await self._request("GET", url, params=params or None)).json()

    async def get_text(self, url: str) -> str:
        return (await self._request("GET", url)).text

    async def post_json(self, url: str, body: Any, headers: dict[str, str] | None = None) -> Any:
        return (await self._request("POST", url, json=body, headers=headers)).json()

    async def _wait_turn(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            last = self._last.get(host)
            if last is not None:
                wait = self._min_interval - (self._clock() - last)
                if wait > 0:
                    await self._sleep(wait)
            self._last[host] = self._clock()

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        host = urlsplit(url).netloc
        status: int | None = None
        reason = ""
        for attempt in range(RETRIES + 1):
            if attempt:
                await self._sleep(0.5 * 2 ** (attempt - 1))
            await self._wait_turn(host)
            try:
                resp = await self._client.request(method, url, **kwargs)
            except httpx.TimeoutException as e:
                status, reason = None, f"timeout: {e}"
                continue
            except httpx.TransportError as e:
                status, reason = None, f"network error: {e}"
                continue
            if resp.is_success:
                return resp
            status, reason = resp.status_code, resp.reason_phrase
            if status not in RETRY_STATUSES:
                break
        raise FetchError(url, status, reason)
