# SentinelX

**SentinelX** is a high-performance, real-time fraud detection and intelligence platform backend designed to ingest financial transactions, evaluate risk vectors, and prevent fraudulent activity.

---

## Tech Stack

- **Runtime & Language**: Python 3.11
- **API Framework**: FastAPI & Starlette
- **Data Validation & Settings**: Pydantic v2 & Pydantic Settings
- **ORM & Migrations**: SQLAlchemy 2.x & Alembic
- **Database**: PostgreSQL 18 (with native `uuidv7()`)
- **Testing**: pytest & HTTPX TestClient

---

## Architectural & Design Decisions

- **No Hard Delete**: Financial records are immutable audit artifacts. Deletions are strictly prohibited; records transition through defined lifecycles (`pending` → `completed` | `failed` | `cancelled`).
- **Idempotency Engine**: Every transaction creation requires an `Idempotency-Key` header (up to 100 characters). Replays with identical payloads return HTTP 200 with the existing record. Key collisions with differing payloads trigger HTTP 409 Conflict. Database unique constraints and atomic race recovery prevent duplicate inserts under high concurrency.
- **Strict Immutability**: Critical transaction fields (`amount`, `sender_account_id`, `receiver_account_id`, `currency`, `transaction_type`, and `occurred_at`) cannot be modified after creation. Only `status` (from `pending`) and `description` are mutable via PATCH.
- **Financial Precision (`NUMERIC(18,2)` & `Decimal`)**: Monetary amounts use `NUMERIC(18,2)` at the database level and Python `Decimal` with strict validation (at most 2 decimal places, positive values only). Floating-point loss of precision is rejected.
- **PostgreSQL 18 Native UUIDv7**: Primary keys leverage PostgreSQL 18's native `uuidv7()` server default, offering time-ordered sequential indexing for optimal B-Tree performance.
- **Portable Enums (`VARCHAR` + CHECK Constraints)**: Rather than Postgres native enums (which complicate schema migrations), enums are stored as `VARCHAR` paired with named database-level `CHECK` constraints (`ck_transactions_status`, `ck_transactions_transaction_type`, `ck_transactions_risk_level`).

---

## API Endpoints

### Core & Health

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Platform welcome banner and running status |
| `GET` | `/health` | Application health status |
| `GET` | `/health/database` | Database connectivity probe |

### Transactions API (`/api/v1/transactions`)

| Method | Endpoint | Required Headers / Params | Status Codes | Description |
|---|---|---|---|---|
| `POST` | `/api/v1/transactions` | `Idempotency-Key: <key>` | `201`, `200`, `400`, `409`, `422` | Create transaction. Replays return `200`, conflicting key returns `409`. Initial status is `pending`. |
| `GET` | `/api/v1/transactions` | `limit`, `offset`, `status`, `risk_level`, `min_amount`, `max_amount`, `date_from`, `date_to`, `sender_account_id`, `receiver_account_id` | `200`, `422` | Paginated query ordered by `occurred_at DESC, id DESC`. Returns items list and total count. |
| `GET` | `/api/v1/transactions/{id}` | `id` (UUID path param) | `200`, `404`, `422` | Fetch single transaction by UUID. |
| `PATCH` | `/api/v1/transactions/{id}` | `id` (UUID path param), JSON body (`status`, `description`) | `200`, `404`, `409`, `422` | Update mutable fields. Terminal transitions return `409`. Immutable fields rejected (`422`). |
| `POST` | `/api/v1/transactions/{id}/cancel` | `id` (UUID path param) | `200`, `404`, `409` | Cancel a transaction. Only `pending` transactions can be cancelled; others return `409`. |

---

## Setup & Local Development

### 1. Prerequisites
- Windows 10/11 with PowerShell
- Python 3.11 installed
- PostgreSQL 18 running locally on port 5432

### 2. Virtual Environment & Dependencies
```powershell
# Create virtual environment
python -m venv .venv

# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 3. Environment Configuration
Copy `.env.example` to `.env` and configure your local PostgreSQL credentials:
```powershell
Copy-Item .env.example .env
```
Ensure `.env` contains:
```env
APP_NAME=SentinelX
DEBUG=True
DATABASE_URL=postgresql+psycopg2://<user>:<password>@localhost:5432/sentinelx
```

### 4. Database Setup & Migrations
Create the primary and test databases in PostgreSQL:
```sql
CREATE DATABASE sentinelx;
CREATE DATABASE sentinelx_test;
```
Run Alembic migrations to apply the schema:
```powershell
alembic upgrade head
```

### 5. Running the Application
Start the Uvicorn ASGI server from the repository root:
```powershell
python -m uvicorn app.main:app --reload --app-dir backend
```
The server will be available at `http://127.0.0.1:8000`. Interactive OpenAPI documentation is accessible at `http://127.0.0.1:8000/docs`.

### 6. Running Automated Tests
Tests execute against the isolated `sentinelx_test` database and automatically clean up between test runs:
```powershell
python -m pytest backend/tests/ -v
```

---

## Project Roadmap

- [x] **Phase 1: Foundation & Infrastructure** — Project structure, configuration hardening, database connection pooling, health checks.
- [x] **Phase 2: Transaction Management Engine** — PostgreSQL 18 schema with UUIDv7, idempotency mechanism, strict Pydantic v2 validation, CRUD service, API v1 routes, DB constraint checks, and comprehensive automated test suite.
- [ ] **Phase 3: Fraud Engine & Rule Evaluator** — Rule evaluation pipeline, velocity checks, risk scoring (0-100), risk tiers (low, medium, high).
- [ ] **Phase 4: Real-time Streaming & Ingestion** — Event streaming integration (Kafka / Redis Streams), asynchronous task workers.
- [ ] **Phase 5: Machine Learning Scoring & Intelligence** — Real-time feature store, anomaly detection models, shadow scoring mode.
