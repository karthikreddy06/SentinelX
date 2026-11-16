# Changelog

All notable changes to SentinelX are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.0.2] - 2026-11-13

### Changed
- **PostgreSQL 18**: Switched Docker Compose and CI services to `postgres:18`.
- **Native UUIDv7**: Removed legacy SQL `uuidv7()` polyfill in Alembic; relies exclusively on PostgreSQL 18 core native UUIDv7 generation.
- **Model Card**: Updated held-out test evaluation with explicit metrics on 298 samples (15 fraud, 5.03% prevalence, 1.0% label noise). Disclosed that Hybrid blend yields lower F1 than ML-only (0.48 vs 0.67) and documented Rules-Only baseline (13.33% recall).
- **Performance Documentation**: Removed obsolete "316 ms" note and added Docker container load testing comparison (10.28 req/s sync vs 41.42 req/s async streaming ingestion).

### Added
- **Live Streaming Proof**: Added `backend/scripts/live_streaming_proof.py` verifying the transactional outbox flow from `POST /api/v1/transactions` to Redpanda and asynchronous stream consumer scoring.
- **Docker Load Benchmark**: Added `backend/scripts/docker_load_test.py` measuring containerized HTTP throughput and latencies.
- **UI Screenshots**: Automated Playwright script `scripts/capture_screenshots.js` and added 6 production screenshots in `docs/screenshots/`.
- **Async Header**: Added optional `X-Async-Scoring` header on `POST /api/v1/transactions` to benchmark async ingestion decoupling.

---

## [1.0.1] - 2026-11-12

### Changed
- **Frontend Security**: Eliminated web storage (`localStorage`/`sessionStorage`) for authentication tokens; tokens reside strictly in JavaScript memory state.
- **Security Headers**: Enforced defensive HTTP headers (Content-Security-Policy, X-Frame-Options, X-Content-Type-Options).
- **Dependencies**: Upgraded setuptools to resolve audit advisories.

### Added
- **Integration Test Suite**: Added `backend/tests/test_integration_real.py` marked with `@pytest.mark.integration`.
- **Docker Verification**: Verified all 8 container services running healthy on Docker Desktop.

---

## [1.0.0] - 2026-11-12

### Added
- **Core Engine**: FastAPI application with PostgreSQL 18, SQLAlchemy 2, and Alembic migrations across 14 tables.
- **Financial Guarantees**: Primary keys via `uuidv7()`, `NUMERIC(18,2)` precision, idempotency key engine, and state transition validation.
- **Authentication & RBAC**: JWT access/refresh token rotation, Argon2 password hashing, and role checks (`admin`, `investigator`, `analyst`).
- **Fraud Engine**: 11 deterministic business rules, point-in-time behavioral features, and Random Forest + Isolation Forest hybrid scoring.
- **Mule Ring Detection**: Directed transaction graph analysis with NetworkX, cycle detection (length 3-6), and PageRank centrality.
- **Event Streaming**: Transactional outbox pattern with Redpanda (Kafka API) broker and Celery 5.7 background workers.
- **Dashboard**: Handcrafted zero-dependency vanilla HTML5/CSS/Canvas operations UI.
