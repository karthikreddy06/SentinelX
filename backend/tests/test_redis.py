from datetime import datetime, timezone
from unittest.mock import patch
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core import redis as redis_module
from app.core.rate_limiter import HybridRateLimiter
from app.fraud.cache import (
    get_sender_profile,
    get_transaction_velocity,
    invalidate_sender_profile,
    record_transaction_velocity,
    set_sender_profile,
)


def test_redis_health_endpoint(client: TestClient):
    resp = client.get("/health/redis")
    data = resp.json()
    if resp.status_code == 200:
        assert data["redis"] in ("ok", "disabled")
    else:
        assert resp.status_code == 503
        assert data["redis"] == "unreachable"
    assert "details" in data


def test_sender_profile_cache_hit_and_invalidation():
    if redis_module.get_redis_client() is None:
        pytest.skip("Redis server not running on 127.0.0.1:6379")
    sender_id = f"test_sender_{uuid.uuid4().hex[:8]}"

    # 1. Cache miss initially
    assert get_sender_profile(sender_id) is None

    # 2. Set profile
    profile_data = {
        "total_history_count": 10,
        "avg_amount": 250.0,
        "std_amount": 15.0,
        "median_amount": 250.0,
        "mad_amount": 10.0,
        "prev_receivers": ["r1", "r2"],
        "prev_devices": ["d1"],
        "prev_ips": ["1.1.1.1"],
        "last_tx_country": "US",
        "last_tx_occurred_at": datetime.now(timezone.utc).isoformat(),
    }
    set_sender_profile(sender_id, profile_data)

    # 3. Cache hit
    cached = get_sender_profile(sender_id)
    assert cached is not None
    assert cached["total_history_count"] == 10
    assert cached["avg_amount"] == 250.0

    # 4. Invalidation
    invalidate_sender_profile(sender_id)
    assert get_sender_profile(sender_id) is None


def test_velocity_cache_sliding_window():
    if redis_module.get_redis_client() is None:
        pytest.skip("Redis server not running on 127.0.0.1:6379")
    sender_id = f"test_vel_{uuid.uuid4().hex[:8]}"
    now_ts = datetime.now(timezone.utc).timestamp()

    # Record 3 transactions in past 30 seconds
    record_transaction_velocity(sender_id, 100.0, now_ts - 20)
    record_transaction_velocity(sender_id, 200.0, now_ts - 10)
    record_transaction_velocity(sender_id, 300.0, now_ts)

    vel = get_transaction_velocity(sender_id, now_ts)
    assert vel is not None
    assert vel["vel_1m"] == 3
    assert vel["vel_1h"] == 3
    assert vel["vel_24h"] == 3
    assert vel["amount_24h"] == 600.0


def test_redis_rate_limiter_and_lockout():
    limiter = HybridRateLimiter()
    key = f"test_key_{uuid.uuid4().hex[:6]}"

    # Within limit
    limited, retry = limiter.check_rate_limit(key, limit=3, window_seconds=60)
    assert not limited
    limited, retry = limiter.check_rate_limit(key, limit=3, window_seconds=60)
    assert not limited
    limited, retry = limiter.check_rate_limit(key, limit=3, window_seconds=60)
    assert not limited

    # Exceed limit
    limited, retry = limiter.check_rate_limit(key, limit=3, window_seconds=60)
    assert limited
    assert retry > 0

    # Test lockout
    user_key = f"user_{uuid.uuid4().hex[:6]}"
    for _ in range(4):
        locked, _ = limiter.record_login_failure(user_key, max_attempts=5, window_seconds=60, lockout_seconds=120)
        assert not locked

    # 5th failure triggers lockout
    locked, retry = limiter.record_login_failure(user_key, max_attempts=5, window_seconds=60, lockout_seconds=120)
    assert locked
    assert retry > 0

    # Check lockout
    is_locked, _ = limiter.check_login_lockout(user_key)
    assert is_locked

    # Record login success clears lockout
    limiter.record_login_success(user_key)
    is_locked, _ = limiter.check_login_lockout(user_key)
    assert not is_locked


def test_graceful_fallback_when_redis_is_unreachable(client: TestClient, admin_headers, db_session):
    """
    Simulate complete Redis failure and assert transactions, scoring,
    and rate limiting continue operating without crashing.
    """
    with patch("app.core.redis.get_redis_client", return_value=None), \
         patch("app.fraud.cache.get_redis_client", return_value=None):

        # 1. Health check returns 503 when Redis check fails
        with patch("app.core.redis.check_redis_health", return_value=("unreachable", "Connection refused")):
            resp = client.get("/health/redis")
            assert resp.status_code == 503
            assert resp.json()["redis"] == "unreachable"

        # 2. Transactions API continues to work seamlessly
        tx_data = {
            "amount": "125.50",
            "currency": "USD",
            "sender_account_id": "fallback_sender",
            "receiver_account_id": "fallback_recv",
            "transaction_type": "transfer",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        create_resp = client.post(
            "/api/v1/transactions",
            json=tx_data,
            headers={**admin_headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert create_resp.status_code == 201
        tx_id = create_resp.json()["id"]

        # 3. Scoring continues to work via direct DB fallback
        score_resp = client.post(f"/api/v1/transactions/{tx_id}/score", headers=admin_headers)
        assert score_resp.status_code == 200
        assert "score" in score_resp.json()

        # 4. Rate limiter falls back to in-memory gracefully
        limiter = HybridRateLimiter()
        fb_key = f"fallback_{uuid.uuid4().hex[:6]}"
        is_lim, _ = limiter.check_rate_limit(fb_key, limit=2, window_seconds=30)
        assert not is_lim
        is_lim, _ = limiter.check_rate_limit(fb_key, limit=2, window_seconds=30)
        assert not is_lim
        is_lim, retry = limiter.check_rate_limit(fb_key, limit=2, window_seconds=30)
        assert is_lim
        assert retry > 0
