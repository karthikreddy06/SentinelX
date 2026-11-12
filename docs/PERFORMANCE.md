# SentinelX Performance & Latency Profile

## Overview
SentinelX is engineered for low-latency real-time fraud detection. Every transaction passes through point-in-time behavioral feature extraction (17 distinct features) and hybrid scoring (10 deterministic rules + Random Forest / Isolation Forest ensemble).

---

## Latency Profiles (Measured on Local PostgreSQL 18 + Python 3.11)

| Pipeline Stage | Cold Path (DB Direct / Cache Miss) | Hot Path (In-Memory / Redis Hit) |
|---|---|---|
| **Sender Profile Lookup** | ~18 ms (unbounded history) / **~4 ms** (indexed) | **< 0.5 ms** |
| **Velocity Windows (1m, 1h, 24h)** | ~12 ms (24h index slice) | **< 0.8 ms** (Redis sorted set) |
| **Pass-Through Detection** | ~6 ms (receiver index slice) | ~5 ms (receiver index slice) |
| **Rule Engine (10 Rules)** | ~0.4 ms | ~0.4 ms |
| **ML Inference (Random Forest + Isolation Forest)** | ~1.2 ms | ~1.2 ms |
| **Total Scoring Pipeline** | **~50.3 ms** | **~37.6 - 45.1 ms** |

---

## Indexing Architecture

To support high-throughput financial stream processing without full-table scans, the following composite B-tree indexes are enforced:

1. `ix_transactions_sender_occurred`: `(sender_account_id, occurred_at)`
   - Used for point-in-time historical sender profile construction and 24h structuring/velocity calculations.
2. `ix_transactions_receiver_occurred`: `(receiver_account_id, occurred_at)`
   - Used for 2-hour inbound window queries in pass-through (mule account) fraud detection.
3. `ix_transactions_status`: `(status)`
   - Used for quick status filtering and state transitions.
4. `ix_transactions_occurred_at`: `(occurred_at)`
   - Used for chronological audit trails and time-series extraction.

---

## High-Throughput Concurrent Load Benchmark (M13)

Measured using `backend/scripts/load_test.py` across 100 concurrent requests (concurrency: 8 workers) executing the complete transactional write path: Pydantic v2 strict schema validation $\to$ PostgreSQL transaction commit $\to$ transactional outbox entry recording $\to$ point-in-time behavioral feature extraction $\to$ Rule engine (11 rules) $\to$ ML inference $\to$ audit log recording.

### Real Benchmark Metrics:
- **Total Requests**: 100
- **Concurrency**: 8 concurrent workers
- **Success Rate**: 100% (100 successes, 0 failures)
- **Total Time**: 9.70 seconds
- **Throughput**: **10.31 Transactions / Sec**
- **Min Latency**: 469.89 ms
- **Median (p50) Latency**: 758.35 ms
- **p90 Latency**: 866.89 ms
- **p95 Latency**: 902.44 ms
- **p99 Latency**: 1831.94 ms
- **Max Latency**: 1831.94 ms

---

## Performance Benchmark Summary
Full details and evolution metrics are documented in [BENCHMARK.md](file:///c:/Users/M.Karthik%20Reddy/Documents/projects/SentinelX/docs/BENCHMARK.md).
