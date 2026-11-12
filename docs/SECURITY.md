# SentinelX — Security Architecture & OWASP Review

SentinelX is designed as a mission-critical, real-time financial fraud detection platform. This document outlines the security controls, threat mitigations, and compliance architectures implemented across the codebase.

---

## 1. Threat Model & Security Posture

SentinelX processes financial transactions and sensitive fraud intelligence. Its threat model accounts for:
- **Credential Stuffing & Account Takeover (ATO)**: Automated attacks targeting user accounts.
- **Data Tampering & Integrity Attacks**: Malicious alterations to historical transactions or fraud scores.
- **Privilege Escalation**: Unauthorized analysts attempting to modify alerts or trigger retrain workflows.
- **Denial of Service (DoS)**: High-frequency transaction floods attempting to saturate the scoring engine.
- **Sensitive Data Leakage**: Accidental exposure of database passwords, JWT secrets, or PII in logs or responses.

---

## 2. Authentication & Session Security

### 2.1 Password Hashing with Argon2id
All user passwords are encrypted using state-of-the-art **Argon2id** password hashing (`argon2-cffi`), adhering to OWASP recommendations:
- **Memory Cost (`m`)**: 64 MB (65,536 KiB)
- **Time Cost (`t`)**: 2 iterations
- **Parallelism (`p`)**: 1 thread
- **Hash Length**: 32 bytes with cryptographically secure 16-byte random salts.
- Plaintext passwords are never logged, stored, or cached.

### 2.2 JWT Lifecycle, Token Rotation & Revocation
- **Access Tokens**: Short-lived (default: 15 minutes) signed with HMAC-SHA256 (`HS256`).
- **Refresh Tokens**: Cryptographically secure, unguessable random strings stored in the PostgreSQL `refresh_tokens` table.
- **Refresh Token Rotation**: Every call to `/api/v1/auth/refresh` revokes the old refresh token (`revoked_at` timestamp) and issues a new pair. Reusing a revoked token immediately triggers security invalidation of the entire session.
- **Logout / Revocation**: Explicit `/api/v1/auth/logout` revokes the active token family.

### 2.3 Account Lockout & Brute-Force Defense
- Repeated login failures (5 consecutive invalid password attempts) trigger an automatic temporary lockout for 15 minutes.
- Rate limiting enforces per-IP throttling on authentication routes (`/api/v1/auth/login`).

---

## 3. Role-Based Access Control (RBAC) Matrix

Access to resources is enforced via strict FastAPI dependency injection (`require_roles`):

| Endpoint Route | Analyst | Investigator | Admin | Description |
|---|:---:|:---:|:---:|---|
| `GET /api/v1/transactions` | ✅ | ✅ | ✅ | View and filter transactions |
| `POST /api/v1/transactions` | ❌ | ❌ | ✅ | Create new transaction (API client / admin) |
| `POST /api/v1/transactions/{id}/cancel` | ❌ | ❌ | ✅ | Cancel pending transaction |
| `GET /api/v1/alerts` | ✅ | ✅ | ✅ | View and filter alerts |
| `PATCH /api/v1/alerts/{id}` | ❌ | ✅ | ✅ | Acknowledge, investigate, resolve, dismiss |
| `GET /api/v1/investigations` | ✅ | ✅ | ✅ | View cases |
| `POST /api/v1/investigations` | ❌ | ✅ | ✅ | Create new case |
| `POST /api/v1/investigations/{id}/notes` | ❌ | ✅ | ✅ | Add investigation note |
| `POST /api/v1/investigations/{id}/evidence` | ❌ | ✅ | ✅ | Attach case evidence |
| `POST /api/v1/investigations/{id}/close` | ❌ | ✅ | ✅ | Close case with outcome |
| `GET /api/v1/network/*` | ✅ | ✅ | ✅ | View graph and suspicious accounts |
| `POST /api/v1/network/recalculate` | ❌ | ✅ | ✅ | Trigger graph topology analysis |
| `POST /api/v1/ml/retrain` | ❌ | ❌ | ✅ | Trigger ML model retraining |
| `GET /api/v1/audit-logs` | ❌ | ❌ | ✅ | Query security audit log |
| `GET/POST /api/v1/users` | ❌ | ❌ | ✅ | Provision and manage users |

---

## 4. Data Integrity & Immutability

### 4.1 Transaction Immutability & Soft Deletion
- Financial transactions are immutable once created. Updates are restricted to status and description (`TransactionUpdate` schema with `extra="forbid"`).
- **No Hard Deletes**: The database schema and API enforce a strict zero-hard-delete policy. Transactions cannot be deleted through any API endpoint.
- Cancellation follows a rigorous state machine (`pending` $\to$ `cancelled`). Completed or failed transactions cannot be cancelled.

### 4.2 Transactional Outbox Pattern
To prevent distributed transaction inconsistency and dual-write anomalies between PostgreSQL and Kafka/Redpanda, SentinelX implements the Transactional Outbox pattern:
- Transactions and corresponding `transactions.created` outbox events are committed atomically inside a single ACID database transaction.
- Outbox publisher uses `SELECT ... FOR UPDATE SKIP LOCKED` for lock-free parallel streaming workers.

### 4.3 Database-Level Constraints
Integrity is guaranteed at the database engine level via PostgreSQL CHECK constraints and UUIDv7:
- `ck_transactions_amount_positive`: `amount > 0`
- `ck_transactions_currency_format`: `currency ~ '^[A-Z]{3}$'`
- `ck_transactions_distinct_accounts`: `sender_account_id != receiver_account_id`
- `ck_fraud_scores_range`: `score >= 0 AND score <= 100`
- `idempotency_key` unique index prevents duplicate charges or replayed transactions.

---

## 5. Injection & Input Sanitization

- **SQL Injection**: Prevented universally. 100% of queries use SQLAlchemy 2.0 parameterized expressions (`select()`, `where()`). No raw SQL interpolation or f-string queries are permitted.
- **Pydantic v2 Strict Typing**: All incoming request payloads are strictly validated using Pydantic v2 models with `extra="forbid"`, preventing mass assignment vulnerabilities.
- **Path Traversal**: File references (model artifacts) are anchored strictly to designated absolute directories using `Path.resolve()`.

---

## 6. Sensitive Data Redaction & Logging

- **Redacting JSON Formatter**: Custom log formatter (`backend/app/core/middleware.py`) sanitizes all structured log output:
  - Regex masks password fields, authorization headers, and bearer tokens.
  - Strips database connection strings containing credentials (`postgresql+psycopg2://...`).
- **Startup Credential Audit**: `validate_jwt_secret()` in `backend/app/core/config.py` enforces that in production (`DEBUG=False`), the server will fail to start if the JWT secret is missing, under 32 characters, or a known placeholder.

---

## 7. Denial of Service (DoS) & Rate Limiting

- **Rate Limiting**: Two-tier protection:
  - Hot tier: In-memory token bucket rate limiter for rapid per-client throttling.
  - Distributed tier: Redis sliding window log for multi-node deployments.
  - Fails safe with in-memory fallback if Redis is unavailable.
- **Query Scopes & Pagination**: All collection endpoints enforce maximum `limit` values (e.g., max 100 transactions, max 200 alerts) and indexed timestamp slices (24h/30d lookback) to eliminate unindexed full-table scans.

---

## 8. Security Headers

The application injects OWASP recommended security headers on every HTTP response:
```http
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
X-XSS-Protection: 1; mode=block
Strict-Transport-Security: max-age=31536000; includeSubDomains
Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'
```
