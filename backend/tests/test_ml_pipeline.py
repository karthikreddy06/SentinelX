import copy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.fraud.scorer import default_rules_engine
from app.ml.model_loader import ModelManager, model_manager
from app.ml.predictor import predict_fraud_score
from app.models.transaction import RiskLevel, Transaction, TransactionStatus, TransactionType
from ml.synthetic import generate_synthetic_transactions
from ml.train import extract_features_from_synthetic


# === GENERATOR DETERMINISM & NO LEAKAGE ===

def test_synthetic_generator_determinism():
    data1 = generate_synthetic_transactions(num_accounts=20, days=2, seed=123)
    data2 = generate_synthetic_transactions(num_accounts=20, days=2, seed=123)

    assert len(data1) == len(data2)
    for t1, t2 in zip(data1, data2):
        assert t1["sender_account_id"] == t2["sender_account_id"]
        assert t1["receiver_account_id"] == t2["receiver_account_id"]
        assert t1["amount"] == t2["amount"]
        assert t1["occurred_at"] == t2["occurred_at"]
        assert t1["is_fraud"] == t2["is_fraud"]


def test_no_temporal_train_test_leakage():
    data = generate_synthetic_transactions(num_accounts=30, days=4, seed=42)
    n = len(data)
    train_end = int(n * 0.70)
    test_start = int(n * 0.85)

    train_txs = data[:train_end]
    test_txs = data[test_start:]

    max_train_time = max(t["occurred_at"] for t in train_txs)
    min_test_time = min(t["occurred_at"] for t in test_txs)

    # Train timestamps must be strictly prior to test timestamps
    assert max_train_time <= min_test_time


# === MODEL LOADING, SCORING, AND FALLBACK ===

def test_model_loads_and_scores():
    assert model_manager.is_loaded
    assert model_manager.metadata is not None

    sample_features = {
        "current_amount": 9500.0,
        "velocity_1m_count": 3,
        "velocity_1h_count": 8,
        "velocity_24h_count": 15,
        "velocity_24h_amount": 15000.0,
        "avg_amount": 200.0,
        "std_amount": 50.0,
        "z_score": 186.0,
        "is_first_time_receiver": 1.0,
        "is_new_device": 1.0,
        "is_new_ip": 1.0,
        "impossible_travel": 1.0,
        "is_unusual_hour": 1.0,
        "total_structuring_count": 3.0,
        "is_round_number": 1.0,
        "recent_failures_1h": 2.0,
        "pass_through_detected": 1.0,
    }

    ml_score, top_features, method = predict_fraud_score(sample_features)
    assert ml_score is not None
    assert 0 <= ml_score <= 100
    assert len(top_features) >= 1
    assert "explanation_method" in locals() or method is not None


def test_graceful_fallback_when_model_missing(monkeypatch):
    # Simulate missing model manager
    monkeypatch.setattr(model_manager, "_bundle", None)
    monkeypatch.setattr(model_manager, "_metadata", None)

    sample_features = {"current_amount": 100.0, "z_score": 0.0}
    score, top_f, method = predict_fraud_score(sample_features)
    assert score is None
    assert top_f == []
    assert method is None

    # Scorer must fall back gracefully to rules only
    tx = Transaction(
        amount=100.0,
        sender_account_id="s",
        receiver_account_id="r",
        currency="USD",
        transaction_type=TransactionType.TRANSFER,
    )
    final_score, level, reasons, engine_ver = default_rules_engine.evaluate(tx, sample_features)
    assert 0 <= final_score <= 100
    assert engine_ver == "rules_v1.0.0"  # Rules only, no ML suffix


# === ML ENDPOINTS & RBAC ===

def test_get_model_info_endpoint(client, analyst_headers, investigator_headers):
    # Analyst can view model info
    res = client.get("/api/v1/ml/model", headers=analyst_headers)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "active"
    assert "metadata" in data
    assert data["metadata"]["version"] == "ml_v1.0.0"

    # Unauthenticated -> 401
    assert client.get("/api/v1/ml/model").status_code == 401


def test_retrain_endpoint_rbac(client, admin_headers, investigator_headers):
    # Non-admin cannot retrain -> 403
    r_forbid = client.post("/api/v1/ml/retrain", headers=investigator_headers)
    assert r_forbid.status_code == 403

    # Admin can retrain -> 200
    r_admin = client.post("/api/v1/ml/retrain", headers=admin_headers)
    assert r_admin.status_code == 200
    assert r_admin.json()["status"] in ["promoted", "rejected"]
