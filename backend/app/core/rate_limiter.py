from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
import threading


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
            # Prune old timestamps
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
        """Utility for test isolation."""
        with self._lock:
            self._requests.clear()
            self._failures.clear()
            self._lockouts.clear()


rate_limiter = InMemoryRateLimiter()
