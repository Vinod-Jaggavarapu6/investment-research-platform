import json
import logging
from typing import Any, Optional
from datetime import timedelta

import redis.asyncio as redis
from pydantic import Field
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)


class RedisConfig(BaseSettings):
    host: str = Field(default="localhost", alias="REDIS_HOST")
    port: int = Field(default=6379, alias="REDIS_PORT")
    password: Optional[str] = Field(default=None, alias="REDIS_PASSWORD")
    ssl: bool = Field(default=False, alias="REDIS_SSL")
    max_connections: int = 20

    model_config = {"extra": "ignore", "populate_by_name": True}


class ResearchCacheClient:

    def __init__(self, config: RedisConfig):
        pool_kwargs = dict(
            host=config.host,
            port=config.port,
            max_connections=config.max_connections,
            decode_responses=True,
        )
        # Only add password if it's actually set — redis rejects password=None
        if config.password:
            pool_kwargs["password"] = config.password
        # Only add ssl if enabled — redis rejects ssl=False
        if config.ssl:
            pool_kwargs["ssl"] = True

        self._pool = redis.ConnectionPool(**pool_kwargs)
        self._client = redis.Redis(connection_pool=self._pool)

    async def get(self, key: str) -> Optional[Any]:
        try:
            raw = await self._client.get(key)
            return json.loads(raw) if raw is not None else None
        except Exception as e:
            logger.warning(f"Redis GET error key={key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: timedelta) -> bool:
        try:
            await self._client.setex(
                name=key,
                time=int(ttl.total_seconds()),
                value=json.dumps(value, default=str),
            )
            return True
        except Exception as e:
            logger.warning(f"Redis SET error key={key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        try:
            await self._client.delete(key)
            return True
        except Exception as e:
            logger.warning(f"Redis DELETE error key={key}: {e}")
            return False

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._client.exists(key))
        except Exception:
            return False

    async def health_check(self) -> bool:
        try:
            return await self._client.ping()
        except Exception as e:
            logger.warning(f"Redis health check failed: {e}")
            return False

    async def close(self):
        await self._pool.disconnect()