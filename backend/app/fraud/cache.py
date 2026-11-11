import json
import logging
from typing import Any

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

PROFILE_TTL_SECONDS = 900  # 15 minutes
VELOCITY_TTL_SECONDS = 86400  # 24 hours


def get_sender_profile(sender_id: str) -> dict[str, Any] | None:
    """Retrieve cached sender profile (stats, receiver set, devices) from Redis."""
    client = get_redis_client()
    if client is None:
        return None

    try:
        raw = client.get(f"cache:sender:profile:{sender_id}")
        if raw:
            return json.loads(raw)
        return None
    except Exception as e:
        logger.debug("Redis error in get_sender_profile: %s", e)
        return None


def set_sender_profile(sender_id: str, profile: dict[str, Any], ttl: int = PROFILE_TTL_SECONDS) -> None:
    """Cache sender profile in Redis with TTL."""
    client = get_redis_client()
    if client is None:
        return

    try:
        client.set(
            f"cache:sender:profile:{sender_id}",
            json.dumps(profile),
            ex=ttl,
        )
    except Exception as e:
        logger.debug("Redis error in set_sender_profile: %s", e)


def invalidate_sender_profile(sender_id: str) -> None:
    """Invalidate cached sender profile upon new transaction write."""
    client = get_redis_client()
    if client is None:
        return

    try:
        client.delete(f"cache:sender:profile:{sender_id}")
    except Exception as e:
        logger.debug("Redis error in invalidate_sender_profile: %s", e)


def record_transaction_velocity(sender_id: str, amount: float, occurred_at_ts: float) -> None:
    """Record a transaction in Redis sorted set for sliding window velocity tracking."""
    client = get_redis_client()
    if client is None:
        return

    try:
        key = f"cache:vel:{sender_id}"
        # Member format: "<ts>:<amount>:<nonce>" to ensure uniqueness in sorted set
        member = f"{occurred_at_ts}:{amount}"
        client.zadd(key, {member: occurred_at_ts})
        # Keep 24 hours of window
        cutoff = occurred_at_ts - 86400
        client.zremrangebyscore(key, 0, cutoff)
        client.expire(key, VELOCITY_TTL_SECONDS)
    except Exception as e:
        logger.debug("Redis error in record_transaction_velocity: %s", e)


def get_transaction_velocity(sender_id: str, current_ts: float) -> dict[str, Any] | None:
    """
    Retrieve 1m, 1h, and 24h velocity counts and 24h amount from Redis sorted set.
    Returns None if cache is empty or Redis is unavailable.
    """
    client = get_redis_client()
    if client is None:
        return None

    try:
        key = f"cache:vel:{sender_id}"
        if not client.exists(key):
            return None

        time_1m = current_ts - 60
        time_1h = current_ts - 3600
        time_24h = current_ts - 86400

        # Query members within 24h
        entries = client.zrangebyscore(key, time_24h, current_ts, withscores=True)
        if not entries:
            return {"vel_1m": 0, "vel_1h": 0, "vel_24h": 0, "amount_24h": 0.0}

        vel_1m = 0
        vel_1h = 0
        vel_24h = len(entries)
        amount_24h = 0.0

        for member, score in entries:
            if score >= time_1m:
                vel_1m += 1
            if score >= time_1h:
                vel_1h += 1
            parts = member.split(":")
            if len(parts) >= 2:
                try:
                    amount_24h += float(parts[1])
                except ValueError:
                    pass

        return {
            "vel_1m": vel_1m,
            "vel_1h": vel_1h,
            "vel_24h": vel_24h,
            "amount_24h": amount_24h,
        }
    except Exception as e:
        logger.debug("Redis error in get_transaction_velocity: %s", e)
        return None


def clear_all_fraud_cache() -> None:
    """Clear all fraud velocity and profile caches (useful for test isolation)."""
    client = get_redis_client()
    if client is None:
        return

    try:
        keys = client.keys("cache:*")
        if keys:
            client.delete(*keys)
    except Exception as e:
        logger.debug("Redis error in clear_all_fraud_cache: %s", e)
