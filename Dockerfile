# ==============================================================================
# SentinelX Production Multi-Stage Dockerfile
# Python 3.11 Slim | Non-Root User | Optimized Layer Caching
# ==============================================================================

# Stage 1: Build virtual environment & dependencies
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# Stage 2: Production runtime
FROM python:3.11-slim AS runner

WORKDIR /app

# Install runtime PostgreSQL client library and curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pre-built virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV PYTHONPATH="/app:/app/backend"
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Create unprivileged system user and group
RUN groupadd -r -g 10001 sentinelx && \
    useradd -r -u 10001 -g sentinelx -d /app -s /sbin/nologin sentinelx

# Copy application source code and configurations
COPY backend /app/backend
COPY ml /app/ml
COPY frontend /app/frontend
COPY alembic.ini /app/alembic.ini
COPY pyproject.toml /app/pyproject.toml

# Set permissions for unprivileged user
RUN chown -R sentinelx:sentinelx /app

USER sentinelx

EXPOSE 8000

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Default entrypoint starts FastAPI with Uvicorn
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "backend"]
