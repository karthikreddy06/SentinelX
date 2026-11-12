# SentinelX Performance & Latency Profile

## 1. Test Environment & System Specifications

- **Operating System**: Microsoft Windows 11 (x86_64)
- **Runtime**: Python 3.11.0 (64-bit)
- **Database**: PostgreSQL 18.6 on x86_64-windows (port 5432)
- **Cache / Broker**: Redis 7.0 (when containerized); in-memory fallback when offline
- **Concurrency Test Tool**: `backend/scripts/load_test.py` via HTTPX ThreadPoolExecutor

---

## 2. Feature Extraction & Scoring Path Latency

Measured via `backend/scripts/benchmark_scoring.py` (25 iterations, 50 pre-seeded historical transactions per sender):

| Pipeline Stage | Cold Path (DB Direct / Cache Miss) | Redis Cache Offline (Graceful DB Fallback) |
|---|---|---|
| **Sender Profile Lookup** | ~18 ms | ~18 ms |
| **Velocity Windows (1m, 1h, 24h)** | ~12 ms (24h index slice) | ~12 ms (24h index slice) |
| **Pass-Through Detection** | ~6 ms (receiver index slice) | ~6 ms (receiver index slice) |
| **Network Risk Check** | ~4 ms | ~4 ms |
| **Rule Engine (11 Rules)** | ~0.4 ms | ~0.4 ms |
| **ML Inference (RF + IsoForest)** | ~1.2 ms | ~1.2 ms |
| **Total Scoring Latency (Mean)** | **68.02 ms** | **74.66 ms** *(includes connection timeout & fallback)* |
| **Total Scoring Latency (Median)** | **68.16 ms** | **77.28 ms** |
| **Total Scoring Latency (p95)** | **88.14 ms** | **95.42 ms** |

> [!NOTE]
> **Historical Correction**: The earlier claim of "316 ms -> 50 ms" was derived from an unindexed full-table query comparison before composite B-Tree indexes were applied. With composite indexes (`ix_transactions_sender_occurred` and `ix_transactions_receiver_occurred`), real PostgreSQL 18 cold query latency is **68.02 ms**. When Redis is offline, SentinelX seamlessly falls back to PostgreSQL in **74.66 ms** with zero request failures.

---

## 3. High-Throughput Concurrent Write & Evaluation Load Test

Measured using `backend/scripts/load_test.py` executing the complete transactional write path: Pydantic v2 strict schema validation $\to$ PostgreSQL transaction commit $\to$ transactional outbox entry recording $\to$ point-in-time behavioral feature extraction $\to$ Rule engine (11 rules) $\to$ ML inference $\to$ audit log recording.

### Real Benchmark Metrics (100 Requests, Concurrency: 8)
- **Total Requests**: 100
- **Concurrency**: 8 concurrent worker threads
- **Success Rate**: 100% (100 successes, 0 failures, 0 errors)
- **Total Duration**: 9.68 seconds
- **Throughput**: **10.33 Transactions / Sec**
- **Min Latency**: 373.10 ms
- **Median (p50) Latency**: 746.18 ms
- **p90 Latency**: 918.54 ms
- **p95 Latency**: 929.31 ms
- **p99 Latency**: 1,688.06 ms
- **Max Latency**: 1,688.06 ms

---

## 4. Indexing Architecture

To support high-throughput financial stream processing without full-table scans, the following composite B-tree indexes are enforced:

1. `ix_transactions_sender_occurred`: `(sender_account_id, occurred_at)`
   - Used for point-in-time historical sender profile construction and 24h structuring/velocity calculations.
2. `ix_transactions_receiver_occurred`: `(receiver_account_id, occurred_at)`
   - Used for 2-hour inbound window queries in pass-through (mule account) fraud detection.
3. `ix_transactions_status`: `(status)`
   - Used for quick status filtering and state transitions.
4. `ix_transactions_occurred_at`: `(occurred_at)`
   - Used for chronological audit trails and time-series extraction.
