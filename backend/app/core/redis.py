import logging
import time
from typing import Any
import redis
from redis.connection import ConnectionPool

from app.core.config import settings

logger = logging.getLogger(__name__)

_pool: ConnectionPool | None = None
_redis_client: redis.Redis | None = None
_warned_unreachable: bool = False
_is_connected: bool = False
_last_check_time: float = 0.0
_COOLDOWN_SECONDS: float = 10.0


def get_redis_client() -> redis.Redis | None:
    """
    Get a thread-safe Redis client instance with graceful degradation.
    Returns None if Redis is disabled or unreachable.
    Uses a cooldown to prevent blocking requests when Redis is unavailable.
    """
    global _pool, _redis_client, _warned_unreachable, _is_connected, _last_check_time

    if not settings.REDIS_ENABLED:
        return None

    now = time.time()
    if _is_connected and _redis_client is not None:
        return _redis_client

    # Cooldown before re-attempting connection if previously failed
    if not _is_connected and (now - _last_check_time) < _COOLDOWN_SECONDS:
        return None

    _last_check_time = now
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
        _is_connected = True
        _warned_unreachable = False
        return _redis_client
    except (redis.ConnectionError, redis.TimeoutError, redis.RedisError) as e:
        _is_connected = False
        if not _warned_unreachable:
            logger.warning(
                "Redis is unreachable at %s (%s). Falling back gracefully to direct database operations.",
                settings.REDIS_URL,
                str(e),
            )
            _warned_unreachable = True
        return None


def mark_redis_disconnected() -> None:
    """Mark Redis as disconnected so subsequent calls trigger graceful degradation."""
    global _is_connected, _last_check_time
    _is_connected = False
    _last_check_time = time.time()


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
