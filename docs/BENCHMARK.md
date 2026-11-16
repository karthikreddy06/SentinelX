# SentinelX Performance Benchmark: Scoring Path Latency

This document details measured performance benchmarks for SentinelX's fraud feature extraction and scoring path, comparing direct PostgreSQL 18 querying (cold / cache miss) against Redis in-memory caching (hot / cache hit).

---

## 1. Environment & Workload

- **Host OS**: Microsoft Windows 11
- **Runtime**: Python 3.11 (64-bit)
- **Database**: PostgreSQL 18 with composite B-Tree indexes (`ix_transactions_sender_occurred`, `ix_transactions_receiver_occurred`)
- **Cache**: Redis 7.0 with automatic graceful fallback
- **Workload**: Point-in-time feature extraction (17 behavioral features) + hybrid rules (11 rules) + ML inference (`RandomForest` + `IsolationForest`)
- **Sender History Depth**: 50 pre-seeded historical transactions per account
- **Sample Size**: 25 measured iterations
- **Benchmark Script**: `backend/scripts/benchmark_scoring.py`

---

## 2. Measured Scoring Latency

Measured via `backend/scripts/benchmark_scoring.py`:

| Metric | PostgreSQL 18 Direct (Cold / Cache Miss) | Redis Cache Hit (Hot Path) | Redis Offline (Graceful DB Fallback) |
|---|---|---|---|
| **Mean Latency** | **68.02 ms** | **45.06 ms** | **74.66 ms** *(incl. timeout)* |
| **Median Latency (p50)** | **68.16 ms** | **37.57 ms** | **77.28 ms** |
| **95th Percentile (p95)** | **88.14 ms** | **70.64 ms** | **95.42 ms** |

*Takeaway*: With composite B-Tree indexes on `(sender_account_id, occurred_at)` and `(receiver_account_id, occurred_at)`, direct PostgreSQL 18 evaluation averages ~68 ms on this benchmark. When Redis is online, caching reduces median scoring latency to ~38 ms. If Redis is offline or degraded, the platform falls back to PostgreSQL in ~75 ms with zero request failures.

---

## 3. Architecture & Caching Strategy

1. **Sender Profile Cache (`cache:sender:profile:{sender_id}`)**:
   - Caches historical mean, variance, standard deviation, median, median absolute deviation (MAD), receiver sets, device fingerprints, last country, and night hour distribution.
   - TTL: 900 seconds (15 minutes).
   - Invalidation: invalidated on write to prevent stale historical baselines.

2. **Transaction Velocity Sliding Window (`cache:vel:{sender_id}`)**:
   - Redis sorted sets (`ZADD`, `ZRANGEBYSCORE`) tracking 1m, 1h, and 24h counts and cumulative volume.
   - TTL: 86,400 seconds (24 hours).

3. **Hybrid Distributed Rate Limiter (`rl:req:{key}`, `rl:lockout:{key}`)**:
   - Multi-worker safe distributed rate limiter with automatic thread-safe in-memory sliding window fallback when Redis is unreachable.
