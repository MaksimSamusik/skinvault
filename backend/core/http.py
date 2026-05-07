import asyncio
import time
from typing import Optional

import httpx

from core.config import DEFAULT_USER_AGENT, STEAM_RATE_LIMIT_PER_MIN

_client: Optional[httpx.AsyncClient] = None


def get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT},
        )
    return _client


async def close_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


class RateLimiter:
    """Простой rate-limiter, ограничивает запросы по N в минуту."""

    def __init__(self, per_minute: int):
        self.min_interval = 60.0 / max(1, per_minute)
        self._last_call = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._last_call + self.min_interval - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_call = time.monotonic()


steam_market_limiter = RateLimiter(STEAM_RATE_LIMIT_PER_MIN)
