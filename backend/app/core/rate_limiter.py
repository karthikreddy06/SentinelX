from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
import logging
import threading
import uuid

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)


class RateLimiter(ABC):
    """Abstract interface for rate limiting (supports in-memory or Redis backends)."""

    @abstractmethod
    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        """
        Check if an action is rate limited.
        Returns (is_limited, retry_after_seconds).
        """
        pass

    @abstractmethod
    def record_login_failure(
        self, key: str, max_attempts: int, window_seconds: int, lockout_seconds: int
    ) -> tuple[bool, int]:
        """Record a login failure and evaluate lockout. Returns (is_locked, retry_after)."""
        pass

    @abstractmethod
    def check_login_lockout(self, key: str) -> tuple[bool, int]:
        """Check if an identity/IP is currently locked out. Returns (is_locked, retry_after)."""
        pass

    @abstractmethod
    def record_login_success(self, key: str) -> None:
        """Clear login failure history on successful authentication."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Clear rate limit data for test isolation."""
        pass


class InMemoryRateLimiter(RateLimiter):
    """Thread-safe in-memory rate limiter with sliding window tracking and lockout support."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = defaultdict(list)
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._lockouts: dict[str, float] = {}

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        now = self._now()
        threshold = now - window_seconds

        with self._lock:
            timestamps = [ts for ts in self._requests[key] if ts > threshold]
            if len(timestamps) >= limit:
                oldest = timestamps[0]
                retry_after = max(1, int(oldest + window_seconds - now))
                self._requests[key] = timestamps
                return True, retry_after

            timestamps.append(now)
            self._requests[key] = timestamps
            return False, 0

    def check_login_lockout(self, key: str) -> tuple[bool, int]:
        now = self._now()
        with self._lock:
            locked_until = self._lockouts.get(key)
            if locked_until and locked_until > now:
                retry_after = max(1, int(locked_until - now))
                return True, retry_after
            if locked_until:
                del self._lockouts[key]
            return False, 0

    def record_login_failure(
        self, key: str, max_attempts: int, window_seconds: int, lockout_seconds: int
    ) -> tuple[bool, int]:
        now = self._now()
        threshold = now - window_seconds

        with self._lock:
            failures = [ts for ts in self._failures[key] if ts > threshold]
            failures.append(now)
            self._failures[key] = failures

            if len(failures) >= max_attempts:
                lockout_until = now + lockout_seconds
                self._lockouts[key] = lockout_until
                self._failures[key] = []
                return True, lockout_seconds

            return False, 0

    def record_login_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._lockouts.pop(key, None)

    def reset(self) -> None:
        with self._lock:
            self._requests.clear()
            self._failures.clear()
            self._lockouts.clear()


class RedisRateLimiter(RateLimiter):
    """
    Redis-backed sliding window rate limiter and brute-force lockout manager.
    Enables distributed rate limiting across multiple app worker processes.
    """

    def _now(self) -> float:
        return datetime.now(timezone.utc).timestamp()

    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        client = get_redis_client()
        if client is None:
            raise ConnectionError("Redis is not available")

        now = self._now()
        redis_key = f"rl:req:{key}"
        threshold = now - window_seconds

        pipe = client.pipeline()
        pipe.zremrangebyscore(redis_key, 0, threshold)
        pipe.zcard(redis_key)
        pipe.zrange(redis_key, 0, 0, withscores=True)
        results = pipe.execute()

        current_count = results[1]
        oldest_entry = results[2]

        if current_count >= limit:
            oldest_ts = oldest_entry[0][1] if oldest_entry else now
            retry_after = max(1, int(oldest_ts + window_seconds - now))
            return True, retry_after

        # Add member with unique suffix to avoid overwriting identical timestamps
        member = f"{now}:{uuid.uuid4().hex[:6]}"
        pipe = client.pipeline()
        pipe.zadd(redis_key, {member: now})
        pipe.expire(redis_key, window_seconds + 5)
        pipe.execute()

        return False, 0

    def check_login_lockout(self, key: str) -> tuple[bool, int]:
        client = get_redis_client()
        if client is None:
            raise ConnectionError("Redis is not available")

        redis_key = f"rl:lockout:{key}"
        ttl = client.ttl(redis_key)
        if ttl > 0:
            return True, ttl
        return False, 0

    def record_login_failure(
        self, key: str, max_attempts: int, window_seconds: int, lockout_seconds: int
    ) -> tuple[bool, int]:
        client = get_redis_client()
        if client is None:
            raise ConnectionError("Redis is not available")

        now = self._now()
        fail_key = f"rl:fail:{key}"
        lockout_key = f"rl:lockout:{key}"
        threshold = now - window_seconds

        member = f"{now}:{uuid.uuid4().hex[:6]}"
        pipe = client.pipeline()
        pipe.zremrangebyscore(fail_key, 0, threshold)
        pipe.zadd(fail_key, {member: now})
        pipe.expire(fail_key, window_seconds + 5)
        pipe.zcard(fail_key)
        results = pipe.execute()

        failure_count = results[3]
        if failure_count >= max_attempts:
            pipe = client.pipeline()
            pipe.set(lockout_key, "1", ex=lockout_seconds)
            pipe.delete(fail_key)
            pipe.execute()
            return True, lockout_seconds

        return False, 0

    def record_login_success(self, key: str) -> None:
        client = get_redis_client()
        if client is None:
            return

        pipe = client.pipeline()
        pipe.delete(f"rl:fail:{key}")
        pipe.delete(f"rl:lockout:{key}")
        pipe.execute()

    def reset(self) -> None:
        client = get_redis_client()
        if client is None:
            return

        keys = client.keys("rl:*")
        if keys:
            client.delete(*keys)


class HybridRateLimiter(RateLimiter):
    """
    Hybrid rate limiter that preferentially uses Redis and falls back to
    InMemoryRateLimiter seamlessly if Redis is offline or disabled.
    """

    def __init__(self) -> None:
        self.redis_limiter = RedisRateLimiter()
        self.memory_limiter = InMemoryRateLimiter()

    def check_rate_limit(
        self, key: str, limit: int, window_seconds: int
    ) -> tuple[bool, int]:
        try:
            if get_redis_client() is not None:
                return self.redis_limiter.check_rate_limit(key, limit, window_seconds)
        except Exception as e:
            logger.debug("Redis rate limit check failed, falling back to in-memory: %s", e)
        return self.memory_limiter.check_rate_limit(key, limit, window_seconds)

    def check_login_lockout(self, key: str) -> tuple[bool, int]:
        try:
            if get_redis_client() is not None:
                return self.redis_limiter.check_login_lockout(key)
        except Exception as e:
            logger.debug("Redis lockout check failed, falling back to in-memory: %s", e)
        return self.memory_limiter.check_login_lockout(key)

    def record_login_failure(
        self, key: str, max_attempts: int, window_seconds: int, lockout_seconds: int
    ) -> tuple[bool, int]:
        try:
            if get_redis_client() is not None:
                return self.redis_limiter.record_login_failure(
                    key, max_attempts, window_seconds, lockout_seconds
                )
        except Exception as e:
            logger.debug("Redis record failure failed, falling back to in-memory: %s", e)
        return self.memory_limiter.record_login_failure(
            key, max_attempts, window_seconds, lockout_seconds
        )

    def record_login_success(self, key: str) -> None:
        try:
            if get_redis_client() is not None:
                self.redis_limiter.record_login_success(key)
        except Exception as e:
            logger.debug("Redis record success failed: %s", e)
        self.memory_limiter.record_login_success(key)

    def reset(self) -> None:
        try:
            if get_redis_client() is not None:
                self.redis_limiter.reset()
        except Exception as e:
            logger.debug("Redis limiter reset failed: %s", e)
        self.memory_limiter.reset()


# Global hybrid rate limiter instance
rate_limiter: RateLimiter = HybridRateLimiter()
