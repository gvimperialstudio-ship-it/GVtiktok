"""Per-CNPJ, per-channel fixed-window rate limit.

Keys count requests in the current second. They are not stock counters.
"""

from __future__ import annotations

import asyncio
import time

from redis.asyncio import Redis

from erp.domain.enums import RATE_LIMIT_KEY_PREFIX
from erp.domain.errors import ErpError


class RateLimitExceeded(ErpError):
    def __init__(self, cnpj: str, channel: str) -> None:
        super().__init__(f"Rate limit exceeded for cnpj {cnpj} channel {channel}")


class RedisRateLimiter:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis

    async def acquire(self, cnpj: str, channel: str, requests_per_second: int) -> None:
        if requests_per_second <= 0:
            raise RateLimitExceeded(cnpj, channel)
        for _attempt in range(5):
            second = int(time.time())
            key = f"{RATE_LIMIT_KEY_PREFIX}:{cnpj}:{channel}:{second}"
            count = int(await self.redis.incr(key))
            if count == 1:
                await self.redis.expire(key, 2)
            if count <= requests_per_second:
                return
            await asyncio.sleep(max(0.01, 1.05 - (time.time() % 1)))
        raise RateLimitExceeded(cnpj, channel)


def requests_per_second(rate_limit_config: dict, channel: str, default: int = 5) -> int:
    """Read the company's JSON config. Missing keys use ``default``."""

    channel_value = rate_limit_config.get(channel, rate_limit_config.get("default_rps", default))
    try:
        parsed = int(channel_value)
    except (TypeError, ValueError):
        return default
    return parsed
