# SentinelX Platform Status & Verification

This document is the verified operational status page for SentinelX. It summarizes all completed components, verified capabilities, and explicit boundaries/limitations as of release **v1.0.2**.

---

## 1. Release & Quality Summary

- **Current Version**: `v1.0.2`
- **Database Engine**: PostgreSQL 18 with native `uuidv7()` (14 relational tables, 0 legacy polyfills)
- **Container Architecture**: Docker Compose (8 services: `postgres`, `redis`, `redpanda`, `api`, `outbox_publisher`, `stream_consumer`, `celery_worker`, `celery_beat`)
- **Automated Test Suite**: **102 tests passing**, 0 skipped, 0 failed inside containerized environment
- **Code Quality**: Ruff clean (0 linter errors across all packages)
- **Frontend**: Zero-dependency vanilla HTML5/CSS/JavaScript with interactive HTML5 Canvas graph

---

## 2. What Is Done & Verified

| Domain | Verified Capabilities | Evidence / Verification Method |
|---|---|---|
| **PostgreSQL 18 & Schemas** | - 14 normalized tables with strict foreign keys and check constraints<br>- Native `uuidv7()` primary keys for chronological B-Tree clustering<br>- Reversible Alembic migrations (verified fresh upgrade $\to$ check $\to$ downgrade base $\to$ upgrade) | Dockerized migration lifecycle test on empty DB; zero schema drifts (`alembic check` clean) |
| **Transaction Processing** | - Pydantic v2 strict schema validation with `NUMERIC(18,2)` decimal precision<br>- Idempotency engine (`Idempotency-Key` header with 200 replay vs 409 conflict detection)<br>- Strictly immutable financial history (no hard-deletes, validated state machine) | 33 automated unit tests in `backend/tests/test_transactions_api.py` and `test_db_constraints.py` |
| **Authentication & RBAC** | - Argon2id cryptographic password hashing via `pwdlib`<br>- JWT access tokens (15m expiry) and refresh token rotation with SHA-256 revocation blocklist in Redis<br>- Role-based access control (`admin`, `investigator`, `analyst`)<br>- Immutable `audit_logs` tracking user actions | 15 automated security tests in `backend/tests/test_auth_and_security.py` |
| **Deterministic Rules Engine** | - 11 transparent business rules (velocity bursts, amount anomaly, first-time receiver, unusual hour, impossible travel, new device/IP, structuring, round numbers, pass-through accounts, burst failures, and network mule/hub flags)<br>- Point-in-time behavioral feature extraction with zero future temporal leakage | 10 unit tests in `backend/tests/test_fraud_engine.py` verifying each rule trigger boundary |
| **Machine Learning** | - Supervised Random Forest ($n=100$) + Unsupervised Isolation Forest<br>- 17 point-in-time features<br>- Automated fallback to 100% rules when models are missing<br>- Evaluated on held-out test split (298 samples, 15 fraud cases, 5.03% prevalence, 1.0% noise) | Model evaluated on synthetic split (PR-AUC: 0.8356, ROC-AUC: 0.9764, Recall: 86.67%, Precision: 54.17%) |
| **Transactional Outbox & Streaming** | - Atomic `outbox_events` emission alongside transaction commits<br>- Concurrency-safe publisher polling via `SELECT FOR UPDATE SKIP LOCKED`<br>- Redpanda (Kafka API) broker publishing to `transactions.created` with DLQ escalation<br>- Stream consumer real-time scoring and automated alert creation | Live trace verified in `backend/scripts/live_streaming_proof.py`; automated tests in `test_streaming.py` |
| **Celery Background Workers** | - Background asynchronous scoring pipeline<br>- Celery Beat periodic scheduler (stale alert auto-escalation, token cleanup, published outbox pruning) | Worker and Beat execution logs verified inside Docker container; tests in `test_celery.py` |
| **Network Graph Analysis** | - Directed transaction topology graph using NetworkX<br>- Money mule cycle detection (length 3 to 6 loops)<br>- Aggregator fan-in and distributor fan-out hub identification with PageRank centrality | 8 automated tests in `test_network.py` and API endpoints under `/api/v1/network` |
| **Vanilla Operations Dashboard** | - Zero external framework dependencies, zero build steps, zero third-party CDNs<br>- Force-directed HTML5 Canvas graph layout with physics simulation, zoom, and pan<br>- Slide-over transaction risk inspection drawer detailing transparent rule contributions<br>- Complete alert triage board and investigation case manager | 6 production screenshots captured via Playwright in `docs/screenshots/` and embedded in `README.md` |
| **Security & Containers** | - Non-root container execution (`uid=10001`)<br>- Zero `.env` files or secrets baked into image layers<br>- Content-Security-Policy and hardened headers enforced<br>- Persistent Docker volumes across container lifecycle | Verified via `docker compose down` and `up -d` with 100% data preservation |

---

## 3. What Is NOT Verified / Known Operational Limitations

The following items are outside the verified scope of SentinelX and should be understood by evaluators:

1. **Synthetic Training Distribution**:
   - The ML ensemble is trained and evaluated **exclusively on synthetic data** generated by `ml/synthetic.py` with 1.0% random label flipping.
   - **These metrics do not represent real-world fraud performance** on live banking traffic, where fraudsters actively adapt and execute zero-day evasion tactics.
2. **Hybrid Blending Weights**:
   - The 60% ML / 40% Rules hybrid blending formula is an empirical heuristic that was **not cross-validated** across diverse institutional portfolios.
   - On the synthetic held-out test set, the hybrid blend yielded a lower F1 score (`0.4762`) than ML-only (`0.6667`) due to rules-only recall suppression.
3. **Single-Node Broker Scope**:
   - Event streaming is configured for a single-node Redpanda broker.
   - Multi-partition rebalancing across distributed physical nodes, Confluent Schema Registry integration, and Kafka exactly-once semantics have not been benchmarked.
4. **Single-Node Database Topology**:
   - PostgreSQL 18 performance and migrations were validated on a standalone instance.
   - Multi-region active-active clustering, distributed consensus, or sharded petabyte-scale ledger performance was not tested.
5. **Local Benchmark Hardware**:
   - Load test metrics (10.28 req/s sync, 41.42 req/s async) reflect a single local benchmark run (100 requests @ 8 concurrency) on a development laptop (Intel Core i5-1240P, 16 GB RAM).
   - Async mode measures ingestion latency only; it is not a like-for-like measurement with synchronous scoring.
