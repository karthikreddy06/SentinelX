# SentinelX Performance Benchmark: Scoring Path Latency

This document details the real, measured performance benchmarks for SentinelX's fraud feature extraction and scoring hot path, comparing direct PostgreSQL querying (cold / cache miss) against Redis 7 in-memory caching (hot / cache hit).

---

## 1. Environment & Workload

- **OS**: Windows 11
- **Python**: 3.11.0 (CPython)
- **Database**: PostgreSQL 18
- **Cache**: Redis 7.0 (Alpine container on `127.0.0.1:6379`)
- **Workload**: Real point-in-time feature extraction (17 features including historical statistics, z-score, velocity windows, receiver sets, IP/device anomaly checks) + hybrid rules & ML inference (`RandomForest` + `IsolationForest`).
- **Sender History Depth**: 50 pre-seeded historical transactions.
- **Sample Size**: 25 measured iterations.

---

## 2. Benchmark Results

| Metric | Cold Path (DB Direct / Cache Miss) | Hot Path (Redis Cache Hit) | Improvement |
|---|---|---|---|
| **Mean Latency** | **316.51 ms** | **82.11 ms** | **3.85x speedup (74.1% reduction)** |
| **Median Latency (p50)** | **306.65 ms** | **82.61 ms** | **3.71x speedup (73.1% reduction)** |
| **95th Percentile (p95)** | **391.91 ms** | **95.37 ms** | **4.11x speedup (75.7% reduction)** |

---

## 3. Architecture & Caching Strategy

1. **Sender Profile Cache (`cache:sender:profile:{sender_id}`)**:
   - Caches historical mean, variance, standard deviation, median, median absolute deviation (MAD), previous receiver sets, device fingerprints, and last known geographic location.
   - TTL: 900 seconds (15 minutes).
   - Invalidation: updated or invalidated on new transaction write.

2. **Transaction Velocity Sliding Window (`cache:vel:{sender_id}`)**:
   - Redis sorted sets (`ZADD`, `ZRANGEBYSCORE`) tracking 1m, 1h, and 24h count and cumulative amount windows.
   - TTL: 86,400 seconds (24 hours).

3. **Hybrid Distributed Rate Limiter (`rl:req:{key}`, `rl:lockout:{key}`)**:
   - Redis sorted sets for sliding window login and API rate limiting.
   - Multi-worker safe.
   - Graceful fallback: If Redis is offline or disconnected, automatically degrades to thread-safe in-memory sliding window rate limiting.
