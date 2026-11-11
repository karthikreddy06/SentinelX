# SentinelX Engineering Progress

SentinelX is a real-time financial fraud detection & intelligence platform. This document tracks the implementation progress across all milestones M1 through M15.

---

## Part A Milestones (M3 to M8)

| Milestone | Title | Branch | Status | Evidence & Test Counts |
|---|---|---|---|---|
| **M1** | Core Foundation & Health Checks | `main` | **done** | Base FastAPI app, DB connection pool, `/`, `/health`, `/health/database` |
| **M2** | Transaction Management Engine | `phase-2-transaction-engine` | **done** | 33 passed tests, PostgreSQL 18 uuidv7, idempotency engine, PR #1 merged |
| **M3** | Auth & Security | `phase-3-auth-security` | **done** | 43 passed tests, Argon2 hashing, JWT access & refresh tokens rotation/revocation, RBAC dependencies, in-memory rate limiting & lockout, audit logging table & endpoints, CLI admin provisioning, PR #2 merged |
| **M4** | Fraud Risk Scoring Engine | `phase-4-risk-engine` | **done** | 53 passed tests, 10 transparent rules (velocity, amount anomaly, first-time receiver, unusual hour, impossible travel, new device/ip, structuring, round number, pass-through, burst failures), point-in-time feature extraction, persistent `fraud_scores` table, synchronous creation scoring, POST `/transactions/{id}/score` and GET `/transactions/{id}/risk` |
| **M5** | Machine Learning | `phase-5-ml` | **done** | 59 passed tests, deterministic synthetic data generator, point-in-time training pipeline (RandomForest + IsolationForest, 17 features, 70/15/15 temporal split), Model Card, model loader & predictor with graceful rules-only fallback, hybrid 60/40 scoring, GET `/api/v1/ml/model` and POST `/api/v1/ml/retrain` endpoints |
| **M6** | Fraud Alert Management | `phase-6-alerts` | **done** | 63 passed tests, alerts & alert_events tables with uuidv7, auto-generation on score >= 70 or HIGH risk, alert deduplication, strict state machine transitions with RBAC and resolution notes enforcement, timeline history, stats summary endpoint |
| **M7** | Investigation Management | `phase-7-investigations` | **todo** | Pending |
| **M8** | Redis Caching | `phase-8-redis` | **todo** | Pending |

---

## Part B Milestones (Deferred to Part B)

| Milestone | Title | Status |
|---|---|---|
| **M9** | Streaming Ingestion (Redpanda/Kafka) | **deferred to Part B** |
| **M10** | Async Worker Pipeline (Celery) | **deferred to Part B** |
| **M11** | External Threat Feeds & Watchlists | **deferred to Part B** |
| **M12** | Analytics & Real-Time Reporting Dashboard | **deferred to Part B** |
| **M13** | Vanilla Frontend UI (No Frameworks) | **deferred to Part B** |
| **M14** | Production Hardening & Full Docker Compose | **deferred to Part B** |
| **M15** | End-to-End Simulation & Verification | **deferred to Part B** |
