"""
Dockerized API Load Test: Synchronous Scoring vs Async Streaming Ingestion.
Runs 100 requests at concurrency=8 for each mode against the live running API.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import statistics
import time
import uuid

import httpx

from app.core.security import create_access_token
from app.database.connection import SessionLocal
from app.models.user import User, UserRole


def get_auth_token() -> str:
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.role == UserRole.ADMIN).first()
        if not user:
            user = User(
                email=f"loadtest_admin_{uuid.uuid4().hex[:6]}@sentinelx.local",
                hashed_password="dummy",
                role=UserRole.ADMIN,
                full_name="LoadTest Admin",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        return create_access_token(
            user_id=str(user.id),
            email=user.email,
            role=user.role.value,
        )
    finally:
        db.close()


def run_benchmark(
    base_url: str,
    token: str,
    async_scoring: bool,
    num_requests: int = 100,
    concurrency: int = 8,
) -> dict:
    mode_label = "Async Streaming Ingestion" if async_scoring else "Synchronous Scoring"
    print("\n=======================================================")
    print(f"Running Benchmark: {mode_label}")
    print(f"Requests: {num_requests}, Concurrency: {concurrency}")
    print("=======================================================")

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "X-Async-Scoring": "true" if async_scoring else "false",
    }

    latencies_ms: list[float] = []
    errors: list[str] = []

    def send_one(i: int) -> float:
        payload = {
            "sender_account_id": f"PERF-ACC-{i % 20:03d}",
            "receiver_account_id": f"PERF-REC-{(i + 3) % 20:03d}",
            "amount": f"{round(40.0 + (i * 9.25) % 950.0, 2):.2f}",
            "currency": "USD",
            "transaction_type": "transfer",
            "channel": "web",
            "device_id": f"DEV-PERF-{i % 10}",
            "ip_address": f"192.168.1.{20 + (i % 40)}",
            "country_code": "US",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        idemp = f"perf-{mode_label[:3].lower()}-{uuid.uuid4()}"
        req_headers = {**headers, "Idempotency-Key": idemp}

        t0 = time.perf_counter()
        with httpx.Client(base_url=base_url, timeout=30.0) as client:
            resp = client.post("/api/v1/transactions", json=payload, headers=req_headers)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if resp.status_code == 201:
            data = resp.json()
            if async_scoring:
                # In async mode, transaction is pending and risk_score is None
                assert data["risk_score"] is None
            else:
                # In sync mode, transaction is scored inline
                assert data["risk_score"] is not None
            return elapsed_ms
        else:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")

    # Warmup 5 requests
    for w in range(5):
        try:
            send_one(w + 1000)
        except Exception:
            pass

    start_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(send_one, i) for i in range(num_requests)]
        for f in as_completed(futures):
            try:
                lat = f.result()
                latencies_ms.append(lat)
            except Exception as e:
                errors.append(str(e))

    total_duration = time.perf_counter() - start_total
    success_count = len(latencies_ms)
    error_count = len(errors)

    latencies_sorted = sorted(latencies_ms)

    def percentile(p: float) -> float:
        if not latencies_sorted:
            return 0.0
        k = (len(latencies_sorted) - 1) * p
        f = int(k)
        c = f + 1
        if c < len(latencies_sorted):
            return latencies_sorted[f] + (k - f) * (latencies_sorted[c] - latencies_sorted[f])
        return latencies_sorted[f]

    metrics = {
        "mode": mode_label,
        "total_requests": num_requests,
        "concurrency": concurrency,
        "successful": success_count,
        "errors": error_count,
        "duration_sec": round(total_duration, 3),
        "throughput_rps": round(success_count / total_duration, 2) if total_duration > 0 else 0.0,
        "min_ms": round(latencies_sorted[0], 2) if latencies_sorted else 0.0,
        "p50_ms": round(percentile(0.50), 2),
        "p90_ms": round(percentile(0.90), 2),
        "p95_ms": round(percentile(0.95), 2),
        "p99_ms": round(percentile(0.99), 2),
        "max_ms": round(latencies_sorted[-1], 2) if latencies_sorted else 0.0,
        "mean_ms": round(statistics.mean(latencies_ms), 2) if latencies_ms else 0.0,
    }

    print(f"Results for {mode_label}:")
    print(f"  Throughput: {metrics['throughput_rps']} req/s")
    print(f"  Duration:   {metrics['duration_sec']} s")
    print(f"  Errors:     {metrics['errors']}")
    print(f"  Min:        {metrics['min_ms']} ms")
    print(f"  p50:        {metrics['p50_ms']} ms")
    print(f"  p90:        {metrics['p90_ms']} ms")
    print(f"  p95:        {metrics['p95_ms']} ms")
    print(f"  p99:        {metrics['p99_ms']} ms")
    print(f"  Max:        {metrics['max_ms']} ms")
    return metrics


if __name__ == "__main__":
    token = get_auth_token()
    base_url = "http://127.0.0.1:8000"

    sync_metrics = run_benchmark(
        base_url=base_url,
        token=token,
        async_scoring=False,
        num_requests=100,
        concurrency=8,
    )

    async_metrics = run_benchmark(
        base_url=base_url,
        token=token,
        async_scoring=True,
        num_requests=100,
        concurrency=8,
    )

    print("\n\n=== FINAL BENCHMARK SUMMARY (JSON) ===")
    print(json.dumps({"sync": sync_metrics, "async": async_metrics}, indent=2))

