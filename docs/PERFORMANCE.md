# SentinelX Performance & Latency Profile

## 1. Test Environment & System Specifications

- **Host CPU**: 12th Gen Intel(R) Core(TM) i5-1240P (12 cores / 16 threads, up to 4.4 GHz)
- **Host RAM**: 16.0 GB (15.7 GB usable)
- **Host OS**: Microsoft Windows 11 Home (x86_64)
- **Container Runtime**: Docker Desktop 29.6.2 (Linux containers, WSL2 backend)
- **Database**: PostgreSQL 18.7 on Linux container (`postgres:18`) with native `uuidv7()`
- **Cache / Broker**: Redis 7.0 (Alpine container), Redpanda v24.2.14 (Kafka API)
- **Runtime**: Python 3.11 (64-bit Debian slim container)
- **Benchmark Tool**: `backend/scripts/docker_load_test.py` via HTTPX concurrent ThreadPoolExecutor against containerized API

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

With composite indexes (`ix_transactions_sender_occurred` and `ix_transactions_receiver_occurred`), PostgreSQL 18 cold query latency averages **68.02 ms**. When Redis is offline, SentinelX seamlessly falls back to PostgreSQL in **74.66 ms** with zero request failures.

---

## 3. High-Throughput Concurrent Write & Evaluation Load Test

Measured using `backend/scripts/docker_load_test.py` against the live Dockerized SentinelX API (`sentinelx-api` on `http://127.0.0.1:8000`), testing both ingestion architectures:
1. **Synchronous Scoring**: Transaction insert + transactional outbox write + synchronous point-in-time feature extraction + 11 fraud rules + ML inference + audit logging before returning response.
2. **Async Streaming Ingestion**: Atomic transaction insert + transactional outbox write + audit logging before returning response; fraud scoring and alert generation proceed asynchronously via Redpanda streaming pipeline (`sentinelx-outbox-publisher` $\to$ Redpanda $\to$ `sentinelx-stream-consumer`).

### Comparative Load Test Results (100 Requests, Concurrency: 8)

| Metric | Synchronous Scoring Mode | Async Streaming Ingestion Mode | Delta / Improvement |
|---|---|---|---|
| **Total Requests** | 100 | 100 | - |
| **Concurrency** | 8 worker threads | 8 worker threads | - |
| **Success Rate** | 100% (100/100) | 100% (100/100) | 0 errors |
| **Total Duration** | 9.72 s | 2.41 s | **4.0x faster** |
| **Throughput** | **10.28 req/s** | **41.42 req/s** | **4.0x higher TPS** |
| **Min Latency** | 356.14 ms | 69.63 ms | - |
| **Median (p50) Latency** | 768.74 ms | 171.89 ms | **4.5x reduction** |
| **p90 Latency** | 878.85 ms | 280.02 ms | 3.1x reduction |
| **p95 Latency** | 890.47 ms | 308.41 ms | 2.9x reduction |
| **p99 Latency** | 938.84 ms | 342.90 ms | 2.7x reduction |
| **Max Latency** | 962.00 ms | 345.31 ms | 2.8x reduction |
| **Mean Latency** | 768.44 ms | 189.85 ms | **4.0x reduction** |

Under synchronous scoring, the client waits for full point-in-time behavioral database queries, rule engine checks, and ML model inference. Under asynchronous streaming ingestion, the transactional outbox pattern decouples client HTTP response from heavy feature extraction, scaling throughput by **4x** and slashing median response latency from 768.74 ms down to 171.89 ms while guaranteeing zero event loss.

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
