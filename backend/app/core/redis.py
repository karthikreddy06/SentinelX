import logging
from typing import Any
import redis
from redis.connection import ConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_redis_client: redis.Redis | None = None
_warned_unreachable: bool = False


def get_redis_client() -> redis.Redis | None:
    """
    Get a thread-safe Redis client instance with graceful degradation.
    Returns None if Redis is disabled or unreachable.
    """
    global _pool, _redis_client, _warned_unreachable

    if not settings.REDIS_ENABLED:
        return None

    try:
        if _pool is None:
            _pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=20,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
                decode_responses=True,
            )
        if _redis_client is None:
            _redis_client = redis.Redis(connection_pool=_pool)

        # Quick ping to verify connectivity
        _redis_client.ping()
        _warned_unreachable = False
        return _redis_client
    except (redis.ConnectionError, redis.TimeoutError, redis.RedisError) as e:
        if not _warned_unreachable:
            logger.warning(
                "Redis is unreachable at %s (%s). Falling back gracefully to direct database operations.",
                settings.REDIS_URL,
                str(e),
            )
            _warned_unreachable = True
        return None


def check_redis_health() -> tuple[str, str]:
    """Check Redis health status: ok, unreachable, or disabled."""
    if not settings.REDIS_ENABLED:
        return "disabled", "Redis caching is disabled in configuration"

    try:
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
            decode_responses=True,
        )
        client.ping()
        return "ok", "Redis is connected and responding to PING"
    except Exception as e:
        return "unreachable", f"Redis connection failed: {str(e)}"


def close_redis_connection() -> None:
    """Close Redis pool connections gracefully on application shutdown."""
    global _pool, _redis_client
    if _pool:
        _pool.disconnect()
        _pool = None
    _redis_client = None
