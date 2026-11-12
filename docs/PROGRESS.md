# SentinelX Engineering Progress

SentinelX is a real-time financial fraud detection & intelligence platform. This document tracks the implementation progress across all milestones M1 through M15.

---

## Part A Milestones (M1 to M8B)

| Milestone | Title | Branch | Status | Evidence & Test Counts |
|---|---|---|---|---|
| **M1** | Core Foundation & Health Checks | `main` | **done** | Base FastAPI app, DB connection pool, `/`, `/health`, `/health/database` |
| **M2** | Transaction Management Engine | `phase-2-transaction-engine` | **done** | 33 passed tests, PostgreSQL 18 uuidv7, idempotency engine, PR #1 merged |
| **M3** | Auth & Security | `phase-3-auth-security` | **done** | 43 passed tests, Argon2 hashing, JWT access & refresh tokens rotation/revocation, RBAC dependencies, in-memory rate limiting & lockout, audit logging table & endpoints, CLI admin provisioning, PR #2 merged |
| **M4** | Fraud Risk Scoring Engine | `phase-4-risk-engine` | **done** | 53 passed tests, 10 transparent rules (velocity, amount anomaly, first-time receiver, unusual hour, impossible travel, new device/ip, structuring, round number, pass-through, burst failures), point-in-time feature extraction, persistent `fraud_scores` table, synchronous creation scoring, POST `/transactions/{id}/score` and GET `/transactions/{id}/risk` |
| **M5** | Machine Learning | `phase-5-ml` | **done** | 59 passed tests, deterministic synthetic data generator, point-in-time training pipeline (RandomForest + IsolationForest, 17 features, 70/15/15 temporal split), Model Card, model loader & predictor with graceful rules-only fallback, hybrid 60/40 scoring, GET `/api/v1/ml/model` and POST `/api/v1/ml/retrain` endpoints |
| **M6** | Fraud Alert Management | `phase-6-alerts` | **done** | 63 passed tests, alerts & alert_events tables with uuidv7, auto-generation on score >= 70 or HIGH risk, alert deduplication, strict state machine transitions with RBAC and resolution notes enforcement, timeline history, stats summary endpoint |
| **M7** | Investigation Management | `phase-7-investigations` | **done** | 65 passed tests, investigations, investigation_alerts, investigation_notes, and investigation_evidence tables with uuidv7, case lifecycle with notes and evidence, automatic alert status syncing (open -> investigating -> resolved_*), aggregated chronological timeline, close & reopen workflows, RBAC |
| **M8** | Redis Caching | `phase-8-redis` | **done** | 70 passed tests, Redis 7 docker-compose, graceful degradation wrapper, `/health/redis` endpoint, distributed rate limiting with sliding window, transaction velocity caching (sorted sets), sender profile cache with write invalidation, verified seamless DB fallback when Redis offline, real scoring speedup of 3.85x |
| **M8B** | Part A Hardening & Realistic ML Overhaul | `phase-8b-hardening` | **done** | 71 passed/skipped tests (69 passed, 2 skipped, 0 failed), overhauled synthetic generator with multi-modal spending distributions, stealth fraud, and 1% label noise; realistic test metrics (Precision: 0.5417, Recall: 0.8667, F1: 0.6667, ROC-AUC: 0.9764); rules baseline (Precision: 1.0, Recall: 0.1333, F1: 0.2353); ablation without `current_amount` (ROC-AUC: 0.9651); leakage audit; PR curve across [0.1..0.9]; admin CLI hardened with strict rejection of `--password`; JWT safety validation on startup; 6.3x DB cold query speedup (316ms -> 50ms) via 24h index range queries |

---

## Part B Milestones

| Milestone | Title | Branch | Status |
|---|---|---|---|
| **M9** | Streaming Ingestion (Redpanda/Kafka) | `phase-9-streaming` | **todo** |
| **M10** | Async Worker Pipeline (Celery) | `phase-10-celery` | **todo** |
| **M11** | External Threat Feeds & Watchlists | `phase-11-network` | **todo** |
| **M12** | Analytics & Real-Time Reporting Dashboard | `phase-12-analytics` | **todo** |
| **M13** | Vanilla Frontend UI (No Frameworks) | `phase-13-frontend` | **todo** |
| **M14** | Production Hardening & Full Docker Compose | `phase-14-docker` | **todo** |
| **M15** | End-to-End Simulation & Verification | `phase-15-release` | **todo** |
