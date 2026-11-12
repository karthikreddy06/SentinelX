from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
import statistics
import sys
import time
import uuid

# Add backend and repo root to sys.path
backend_dir = Path(__file__).resolve().parents[1]
repo_root = Path(__file__).resolve().parents[2]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from fastapi.testclient import TestClient
from app.core.security import create_access_token
from app.database.connection import SessionLocal
from app.main import app
from app.models.user import User, UserRole


def run_benchmark(num_requests: int = 200, concurrency: int = 10) -> dict:
    """
    Executes concurrent transaction ingestion and hybrid fraud scoring benchmark.
    Measures throughput (TPS), p50, p95, and p99 latency.
    """
    client = TestClient(app)

    # Obtain admin token for authenticated ingestion
    db = SessionLocal()
    try:
        admin_user = db.query(User).filter(User.role == UserRole.ADMIN).first()
        if not admin_user:
            admin_user = User(
                email=f"bench_admin_{uuid.uuid4().hex[:6]}@sentinelx.local",
                hashed_password="dummy",
                role=UserRole.ADMIN,
                full_name="Bench Admin",
            )
            db.add(admin_user)
            db.commit()
            db.refresh(admin_user)

        token = create_access_token(
            user_id=str(admin_user.id),
            email=admin_user.email,
            role=admin_user.role.value,
        )
    finally:
        db.close()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    latencies_ms: list[float] = []
    success_count = 0
    failure_count = 0

    print(f"Starting load test: {num_requests} requests with concurrency={concurrency}...")

    def make_request(i: int) -> float:
        payload = {
            "sender_account_id": f"BENCH-ACCT-{i % 25:03d}",
            "receiver_account_id": f"BENCH-RECV-{((i + 5) % 25):03d}",
            "amount": f"{round(50.0 + (i * 7.5) % 850.0, 2):.2f}",
            "currency": "USD",
            "transaction_type": "transfer",
            "channel": "web",
            "device_id": f"DEV-BENCH-{i % 15}",
            "ip_address": f"192.168.1.{10 + (i % 50)}",
            "country_code": "US",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
        }
        idemp = f"bench-idemp-{uuid.uuid4()}"
        req_headers = {**headers, "Idempotency-Key": idemp}

        t0 = time.perf_counter()
        res = client.post("/api/v1/transactions", json=payload, headers=req_headers)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        if res.status_code == 201:
            return elapsed_ms
        else:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text}")

    start_total = time.perf_counter()

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(make_request, i) for i in range(num_requests)]
        for f in as_completed(futures):
            try:
                lat = f.result()
                latencies_ms.append(lat)
                success_count += 1
            except Exception as e:
                failure_count += 1
                print(f"Request failed: {e}")

    total_time_s = time.perf_counter() - start_total
    latencies_ms.sort()

    tps = success_count / total_time_s if total_time_s > 0 else 0
    p50 = statistics.median(latencies_ms) if latencies_ms else 0
    p90 = latencies_ms[int(len(latencies_ms) * 0.90)] if latencies_ms else 0
    p95 = latencies_ms[int(len(latencies_ms) * 0.95)] if latencies_ms else 0
    p99 = latencies_ms[int(len(latencies_ms) * 0.99)] if latencies_ms else 0

    results = {
        "total_requests": num_requests,
        "concurrency": concurrency,
        "success_count": success_count,
        "failure_count": failure_count,
        "total_time_seconds": round(total_time_s, 2),
        "throughput_tps": round(tps, 2),
        "min_latency_ms": round(latencies_ms[0], 2) if latencies_ms else 0,
        "median_p50_ms": round(p50, 2),
        "p90_ms": round(p90, 2),
        "p95_ms": round(p95, 2),
        "p99_ms": round(p99, 2),
        "max_latency_ms": round(latencies_ms[-1], 2) if latencies_ms else 0,
    }

    print("\n=== Benchmark Results ===")
    for k, v in results.items():
        print(f"  {k}: {v}")

    return results


if __name__ == "__main__":
    run_benchmark(num_requests=100, concurrency=8)
