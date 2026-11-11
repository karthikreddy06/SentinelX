"""
SentinelX Benchmark: Scoring Path Latency with and without Redis Cache Hit.
Measures real performance numbers on the local machine.
"""
from datetime import datetime, timedelta, timezone
import statistics
import time
import uuid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.connection import SessionLocal
from app.fraud.cache import clear_all_fraud_cache, set_sender_profile
from app.fraud.features import extract_point_in_time_features
from app.fraud.scorer import default_rules_engine
from app.models.transaction import Transaction, TransactionStatus, TransactionType


def seed_benchmark_data(db: Session, sender_id: str, count: int = 50) -> Transaction:
    """Seed historical transactions for sender to represent a realistic workload."""
    now = datetime.now(timezone.utc)
    for i in range(count):
        tx = Transaction(
            amount=50.0 + (i * 2.5),
            currency="USD",
            sender_account_id=sender_id,
            receiver_account_id=f"bench_recv_{i % 5}",
            transaction_type=TransactionType.PAYMENT,
            channel="web",
            country_code="US",
            occurred_at=now - timedelta(minutes=(count - i) * 10),
            idempotency_key=str(uuid.uuid4()),
        )
        db.add(tx)
    db.commit()

    target_tx = Transaction(
        amount=120.0,
        currency="USD",
        sender_account_id=sender_id,
        receiver_account_id="bench_target_recv",
        transaction_type=TransactionType.PAYMENT,
        channel="web",
        country_code="US",
        occurred_at=now,
        idempotency_key=str(uuid.uuid4()),
    )
    db.add(target_tx)
    db.commit()
    db.refresh(target_tx)
    return target_tx


def run_benchmark(iterations: int = 25) -> dict:
    db = SessionLocal()
    sender_id = f"bench_sender_{uuid.uuid4().hex[:8]}"

    try:
        print(f"Seeding 50 historical transactions for sender '{sender_id}'...")
        target_tx = seed_benchmark_data(db, sender_id, count=50)

        # 1. Cold Run (Cache Miss - direct DB query every time)
        cold_latencies = []
        for _ in range(iterations):
            clear_all_fraud_cache()
            t0 = time.perf_counter()
            features = extract_point_in_time_features(db, target_tx)
            score, level, reasons, _ = default_rules_engine.evaluate(target_tx, features)
            t1 = time.perf_counter()
            cold_latencies.append((t1 - t0) * 1000.0)  # ms

        # 2. Hot Run (Cache Hit - pre-warmed Redis cache)
        # Ensure cache is warm
        extract_point_in_time_features(db, target_tx)
        hot_latencies = []
        for _ in range(iterations):
            t0 = time.perf_counter()
            features = extract_point_in_time_features(db, target_tx)
            score, level, reasons, _ = default_rules_engine.evaluate(target_tx, features)
            t1 = time.perf_counter()
            hot_latencies.append((t1 - t0) * 1000.0)  # ms

        avg_cold = statistics.mean(cold_latencies)
        median_cold = statistics.median(cold_latencies)
        p95_cold = sorted(cold_latencies)[int(0.95 * len(cold_latencies))]

        avg_hot = statistics.mean(hot_latencies)
        median_hot = statistics.median(hot_latencies)
        p95_hot = sorted(hot_latencies)[int(0.95 * len(hot_latencies))]

        speedup = avg_cold / avg_hot if avg_hot > 0 else 1.0
        reduction_pct = ((avg_cold - avg_hot) / avg_cold) * 100.0 if avg_cold > 0 else 0.0

        results = {
            "iterations": iterations,
            "cold": {
                "mean_ms": round(avg_cold, 2),
                "median_ms": round(median_cold, 2),
                "p95_ms": round(p95_cold, 2),
            },
            "hot": {
                "mean_ms": round(avg_hot, 2),
                "median_ms": round(median_hot, 2),
                "p95_ms": round(p95_hot, 2),
            },
            "speedup_factor": round(speedup, 2),
            "latency_reduction_pct": round(reduction_pct, 1),
        }

        print("\n=== BENCHMARK RESULTS ===")
        print(f"Iterations: {iterations}")
        print(f"Cold (Cache Miss / DB): Mean={results['cold']['mean_ms']}ms, Median={results['cold']['median_ms']}ms, P95={results['cold']['p95_ms']}ms")
        print(f"Hot  (Redis Cache Hit): Mean={results['hot']['mean_ms']}ms, Median={results['hot']['median_ms']}ms, P95={results['hot']['p95_ms']}ms")
        print(f"Speedup: {results['speedup_factor']}x ({results['latency_reduction_pct']}% reduction in feature extraction + scoring latency)")

        return results
    finally:
        db.close()


if __name__ == "__main__":
    run_benchmark()
