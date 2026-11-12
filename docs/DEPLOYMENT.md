# SentinelX Production Deployment Guide

This guide provides complete instructions for deploying SentinelX to production environments using Docker Compose, container orchestration platforms, or free-tier cloud platforms (Render, Railway, Fly.io).

---

## 1. Architecture & Service Topology

In a complete production deployment, SentinelX runs as an orchestrated multi-service architecture:

| Service | Container / Process | Description | Port | Health Check |
|---|---|---|---|---|
| **API** | `sentinelx-api` | FastAPI REST API & Vanilla UI | `8000` | `GET /health` |
| **PostgreSQL** | `sentinelx-postgres` | Relational database (PostgreSQL 17/18) | `5432` | `pg_isready` |
| **Redis** | `sentinelx-redis` | Cache, Token Blocklist & Celery Broker | `6379` | `redis-cli ping` |
| **Redpanda** | `sentinelx-redpanda` | Kafka-compatible event streaming engine | `9092` | `rpk cluster health` |
| **Outbox Publisher** | `sentinelx-outbox-publisher` | Polling outbox daemon with `SKIP LOCKED` | N/A | Process supervisor |
| **Stream Consumer** | `sentinelx-stream-consumer` | Event stream consumer for real-time scoring | N/A | Process supervisor |
| **Celery Worker** | `sentinelx-celery-worker` | Asynchronous task execution pipeline | N/A | Celery ping |
| **Celery Beat** | `sentinelx-celery-beat` | Periodic maintenance & graph recalculation | N/A | Process supervisor |

---

## 2. Docker Compose Deployment (Self-Hosted / VPS)

### Prerequisites
- Docker Engine 24.0+ and Docker Compose v2.20+
- At least 2 GB RAM and 10 GB disk space

### Step 1: Clone Repository & Configure Environment
```bash
git clone https://github.com/karthikreddy06/SentinelX.git
cd SentinelX

# Generate a strong 32+ character JWT secret
JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")

# Copy environment template
cp .env.example .env
```

Edit `.env` and set:
```ini
APP_NAME=SentinelX
DEBUG=False
JWT_SECRET_KEY=<your-generated-32+-char-jwt-secret>
```

### Step 2: Build & Start Containers
```bash
docker compose up -d --build
```

Verify service health:
```bash
docker compose ps
```

### Step 3: Run Database Migrations
Run Alembic migrations against the production container:
```bash
docker compose exec api alembic upgrade head
```

### Step 4: Create Initial Administrator Account
```bash
docker compose exec api python backend/scripts/create_admin.py \
  --email "admin@yourcompany.com" \
  --password "YourStrongPassword123!"
```

### Step 5: (Optional) Seed Initial Demo Dataset
```bash
docker compose exec api python backend/scripts/seed_demo.py --num-transactions 250
```

Access the intelligence dashboard in your browser:
```
http://<your-server-ip>:8000/
```

---

## 3. Free-Tier Cloud Deployments

SentinelX can be hosted at zero infrastructure cost on modern cloud free tiers.

### Option A: Render.com

Render offers free web services and managed PostgreSQL:

1. **Database**:
   - Create a free PostgreSQL instance on Render.
   - Copy the External Database URL.
2. **Redis**:
   - Create a free Redis instance on [Upstash](https://upstash.com) (free 10,000 commands/day).
   - Copy the Redis connection string (`rediss://...`).
3. **Web Service**:
   - Create a new Web Service connected to your SentinelX GitHub repository.
   - **Environment**: `Docker` (Render automatically detects the root `Dockerfile`).
   - **Plan**: Free.
   - **Health Check Path**: `/health`.
   - **Environment Variables**:
     ```ini
     DEBUG=False
     DATABASE_URL=<Render-PostgreSQL-URL>
     REDIS_URL=<Upstash-Redis-URL>
     REDIS_ENABLED=True
     STREAMING_ENABLED=False  # Keep disabled or connect to CloudKafka / Confluent Cloud
     JWT_SECRET_KEY=<generate-strong-32+-char-secret>
     ```
4. **Post-Deploy Migration**:
   - In the Render shell console:
     ```bash
     alembic upgrade head
     python backend/scripts/create_admin.py --email admin@example.com --password AdminPassword123!
     ```

### Option B: Railway.app

Railway provides unified hosting with built-in PostgreSQL and Redis plugins:

1. Create a new Railway project and select **Deploy from GitHub repo**.
2. Add a **PostgreSQL** database plugin to the project.
3. Add a **Redis** plugin to the project.
4. Link environment variables:
   - `DATABASE_URL`: Set to Railway's `${{Postgres.DATABASE_URL}}`.
   - `REDIS_URL`: Set to Railway's `${{Redis.REDIS_URL}}`.
   - `CELERY_BROKER_URL`: `${{Redis.REDIS_URL}}/1`.
   - `CELERY_RESULT_BACKEND`: `${{Redis.REDIS_URL}}/2`.
   - `JWT_SECRET_KEY`: Set to a cryptographically secure 32+ character random string.
   - `DEBUG`: `False`.
5. Railway automatically builds the multi-stage `Dockerfile` and runs Alembic migrations on startup if configured as pre-deploy command:
   ```bash
   alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT --app-dir backend
   ```

### Option C: Fly.io

1. Install Fly CLI: `flyctl`
2. Launch app:
   ```bash
   fly launch --no-deploy
   ```
3. Provision managed Postgres & Redis:
   ```bash
   fly postgres create --name sentinelx-db --vm-size shared-cpu-1x
   fly postgres attach sentinelx-db

   fly redis create --name sentinelx-redis
   ```
4. Set production secrets:
   ```bash
   fly secrets set JWT_SECRET_KEY="<strong-secret>" DEBUG="False"
   ```
5. Deploy:
   ```bash
   fly deploy
   fly ssh console -C "alembic upgrade head"
   ```

---

## 4. Environment Variables Reference

| Variable | Type | Default | Description |
|---|---|---|---|
| `DATABASE_URL` | String | *Required* | PostgreSQL connection string (`postgresql+psycopg2://user:pass@host:5432/dbname`) |
| `DEBUG` | Boolean | `False` | Debug mode. When `False`, strict JWT validation is enforced. |
| `JWT_SECRET_KEY` | String | *Required* | 32+ character cryptographically random signing secret |
| `JWT_ALGORITHM` | String | `HS256` | JWT signing algorithm |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Integer | `15` | JWT access token lifespan |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Integer | `7` | Refresh token lifespan with rotation |
| `REDIS_URL` | String | `redis://127.0.0.1:6379/0` | Primary Redis database URL |
| `REDIS_ENABLED` | Boolean | `True` | Set to `False` to fall back to DB-only operations |
| `STREAMING_ENABLED` | Boolean | `True` | Enable Redpanda/Kafka event streaming |
| `KAFKA_BOOTSTRAP_SERVERS` | String | `127.0.0.1:9092` | Kafka bootstrap brokers |
| `CELERY_BROKER_URL` | String | `redis://127.0.0.1:6379/1` | Celery broker URL |
| `CELERY_RESULT_BACKEND` | String | `redis://127.0.0.1:6379/2` | Celery results storage backend |
| `CORS_ORIGINS` | JSON List | `["http://127.0.0.1:8000"]` | Allowed CORS origins for external clients |
| `ALERT_AUTO_CREATE_THRESHOLD` | Integer | `70` | Composite risk score threshold for alert creation |

---

## 5. Security & Maintenance Operations

### Zero-Downtime Database Migrations
Always test migrations against staging before applying to production:
```bash
# Check current migration version
alembic current

# Apply new migrations forward
alembic upgrade head

# Rollback one migration if needed
alembic downgrade -1
```

### Rotating JWT Secrets
1. Generate a new 32+ byte hex string: `python3 -c "import secrets; print(secrets.token_hex(32))"`.
2. Update `JWT_SECRET_KEY` in environment variables.
3. Restart API instances. Note: Active access tokens and refresh tokens signed with the old secret will require users to log in again.

### Log Management & Health Monitoring
SentinelX exposes comprehensive health check endpoints:
- `GET /health`: Overall system status
- `GET /health/database`: Database connection ping & latency
- `GET /health/redis`: Redis connection ping & cache availability
- `GET /health/streaming`: Kafka broker connectivity & topic metadata

