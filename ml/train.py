"""
SentinelX Machine Learning Model Training Pipeline.
Trains supervised and unsupervised fraud models on synthetic behavioral features.
"""

from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

# Ensure project root is in sys.path
root_dir = Path(__file__).resolve().parents[1]
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

import joblib
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, IsolationForest, RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from ml.synthetic import FEATURE_COLUMNS, generate_synthetic_transactions


def extract_features_from_synthetic(
    transactions: list[dict[str, Any]],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Compute point-in-time features sequentially across chronological transactions."""
    X_rows: list[list[float]] = []
    y_labels: list[int] = []

    # State trackers for chronological evaluation
    sender_history: dict[str, list[dict[str, Any]]] = {}
    receiver_history: dict[str, set[str]] = {}
    inbound_history: dict[str, list[dict[str, Any]]] = {}

    for tx in transactions:
        t_time = tx["occurred_at"]
        sender = tx["sender_account_id"]
        receiver = tx["receiver_account_id"]
        amt = float(tx["amount"])

        # History strictly before current transaction
        hist = sender_history.get(sender, [])
        known_receivers = receiver_history.get(sender, set())

        # Velocity counts
        t_1m = t_time - timedelta(minutes=1)
        t_1h = t_time - timedelta(hours=1)
        t_24h = t_time - timedelta(hours=24)

        vel_1m = sum(1 for h in hist if h["occurred_at"] >= t_1m)
        vel_1h = sum(1 for h in hist if h["occurred_at"] >= t_1h)
        vel_24h = sum(1 for h in hist if h["occurred_at"] >= t_24h)
        vel_24h_amt = sum(h["amount"] for h in hist if h["occurred_at"] >= t_24h)

        # Statistical aggregates
        if hist:
            amounts = [h["amount"] for h in hist]
            avg_amt = sum(amounts) / len(amounts)
            variance = sum((x - avg_amt) ** 2 for x in amounts) / len(amounts)
            std_amt = math.sqrt(variance) if variance > 0 else 0.0
        else:
            avg_amt = amt
            std_amt = 0.0

        z_score = (amt - avg_amt) / std_amt if std_amt > 0 else 0.0
        is_first_recv = 1.0 if receiver not in known_receivers else 0.0

        # Devices and IPs
        prev_devices = {h["device_id"] for h in hist if h.get("device_id")}
        prev_ips = {h["ip_address"] for h in hist if h.get("ip_address")}
        is_new_dev = 1.0 if (len(hist) >= 2 and tx.get("device_id") not in prev_devices) else 0.0
        is_new_ip = 1.0 if (len(hist) >= 2 and tx.get("ip_address") not in prev_ips) else 0.0

        # Location / travel
        last_tx = hist[-1] if hist else None
        imp_travel = 0.0
        if last_tx and last_tx.get("country_code") != tx.get("country_code"):
            sec = (t_time - last_tx["occurred_at"]).total_seconds()
            if sec < 7200:
                imp_travel = 1.0

        # Hour anomaly
        hour = t_time.hour
        is_unusual_h = 1.0 if (1 <= hour <= 5 and len(hist) >= 5) else 0.0

        # Structuring
        is_struct = 1.0 if (9000.0 <= amt <= 9999.99 or 900.0 <= amt <= 999.99) else 0.0
        struct_24h = sum(
            1 for h in hist if h["occurred_at"] >= t_24h and (9000.0 <= h["amount"] <= 9999.99)
        )
        total_struct = struct_24h + (1.0 if is_struct else 0.0)

        # Round number
        is_round = 1.0 if (amt >= 500.0 and (amt % 100 == 0 or amt % 500 == 0)) else 0.0

        # Recent failures
        failures = sum(1 for h in hist if h["occurred_at"] >= t_1h and h.get("status") == "failed")

        # Pass-through
        t_2h = t_time - timedelta(hours=2)
        inbounds = inbound_history.get(sender, [])
        pass_through = 1.0 if any(
            h["occurred_at"] >= t_2h and h["amount"] >= (amt * 0.75) for h in inbounds
        ) else 0.0

        row = [
            amt,
            float(vel_1m),
            float(vel_1h),
            float(vel_24h),
            float(vel_24h_amt),
            float(avg_amt),
            float(std_amt),
            float(z_score),
            is_first_recv,
            is_new_dev,
            is_new_ip,
            imp_travel,
            is_unusual_h,
            float(total_struct),
            is_round,
            float(failures),
            pass_through,
        ]

        X_rows.append(row)
        y_labels.append(tx["is_fraud"])

        # Update historical state
        if sender not in sender_history:
            sender_history[sender] = []
            receiver_history[sender] = set()
        sender_history[sender].append(tx)
        receiver_history[sender].add(receiver)

        if receiver not in inbound_history:
            inbound_history[receiver] = []
        inbound_history[receiver].append(tx)

    return np.array(X_rows, dtype=np.float32), np.array(y_labels, dtype=np.int32), FEATURE_COLUMNS


def train_models(
    num_accounts: int = 150,
    days: int = 14,
    seed: int = 42,
    output_dir: str = "ml",
) -> dict[str, Any]:
    """Train supervised and unsupervised models with chronological train/val/test splits."""
    print("Generating synthetic data...")
    transactions = generate_synthetic_transactions(
        num_accounts=num_accounts, days=days, seed=seed
    )
    print(f"Generated {len(transactions)} synthetic transactions.")

    print("Extracting features point-in-time...")
    X, y, feature_names = extract_features_from_synthetic(transactions)

    # Time-based split: 70% train, 15% validation, 15% test
    n = len(X)
    train_end = int(n * 0.70)
    val_end = int(n * 0.85)

    X_train, y_train = X[:train_end], y[:train_end]
    X_val, y_val = X[train_end:val_end], y[train_end:val_end]
    X_test, y_test = X[val_end:], y[val_end:]

    print(f"Splits -> Train: {len(X_train)}, Val: {len(X_val)}, Test: {len(X_test)}")
    print(f"Fraud distribution -> Train: {int(y_train.sum())}/{len(y_train)}, Test: {int(y_test.sum())}/{len(y_test)}")

    # 1. Train Supervised Classifier (RandomForest)
    print("Training Supervised Classifier...")
    clf = RandomForestClassifier(
        n_estimators=100,
        max_depth=8,
        min_samples_split=4,
        class_weight="balanced",
        random_state=seed,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)

    # 2. Train Unsupervised Anomaly Detector (IsolationForest)
    print("Training Unsupervised IsolationForest...")
    iso = IsolationForest(
        n_estimators=100,
        contamination=0.05,
        random_state=seed,
        n_jobs=-1,
    )
    # Fit on legitimate transactions in train set
    iso.fit(X_train[y_train == 0])

    # 3. Evaluate on Validation Set
    val_probs = clf.predict_proba(X_val)[:, 1]
    val_pred = (val_probs >= 0.5).astype(int)
    val_f1 = float(f1_score(y_val, val_pred, zero_division=0))
    val_auc = float(roc_auc_score(y_val, val_probs))

    # 4. Evaluate on Held-out Test Set
    test_probs = clf.predict_proba(X_test)[:, 1]
    test_pred = (test_probs >= 0.5).astype(int)

    precision = float(precision_score(y_test, test_pred, zero_division=0))
    recall = float(recall_score(y_test, test_pred, zero_division=0))
    f1 = float(f1_score(y_test, test_pred, zero_division=0))
    roc_auc = float(roc_auc_score(y_test, test_probs))
    pr_auc = float(average_precision_score(y_test, test_probs))
    cm = confusion_matrix(y_test, test_pred).tolist()

    feature_importances = {
        name: round(float(imp), 4)
        for name, imp in zip(feature_names, clf.feature_importances_)
    }

    metrics = {
        "dataset": {
            "synthetic": True,
            "seed": seed,
            "num_accounts": num_accounts,
            "total_transactions": n,
            "train_size": len(X_train),
            "val_size": len(X_val),
            "test_size": len(X_test),
            "fraud_prevalence_test": round(float(y_test.sum() / len(y_test)), 4),
        },
        "validation_metrics": {
            "f1_score": round(val_f1, 4),
            "roc_auc": round(val_auc, 4),
        },
        "test_metrics": {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1_score": round(f1, 4),
            "roc_auc": round(roc_auc, 4),
            "pr_auc": round(pr_auc, 4),
            "confusion_matrix": {
                "tn": cm[0][0],
                "fp": cm[0][1],
                "fn": cm[1][0],
                "tp": cm[1][1],
            },
        },
        "top_features": sorted(
            feature_importances.items(), key=lambda x: x[1], reverse=True
        )[:5],
    }

    # 5. Save Artifacts
    artifacts_dir = Path(output_dir) / "artifacts"
    reports_dir = Path(output_dir) / "reports"
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    reports_dir.mkdir(parents=True, exist_ok=True)

    version = "ml_v1.0.0"
    model_path = artifacts_dir / f"sentinelx_{version}.joblib"
    meta_path = artifacts_dir / f"sentinelx_{version}.json"
    report_path = reports_dir / "metrics.json"

    # Save models bundle
    bundle = {
        "classifier": clf,
        "isolation_forest": iso,
        "feature_names": feature_names,
        "version": version,
    }
    joblib.dump(bundle, str(model_path), compress=3)

    metadata = {
        "version": version,
        "training_date": datetime.now(timezone.utc).isoformat(),
        "model_type": "RandomForest + IsolationForest",
        "data_seed": seed,
        "feature_list": feature_names,
        "metrics": metrics["test_metrics"],
        "top_features": metrics["top_features"],
        "synthetic_data_caveat": "Trained exclusively on synthetic behavioral sequences.",
    }

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2)

    print(f"Model saved to {model_path} ({os.path.getsize(model_path) / 1024:.1f} KB)")
    print(f"Metrics saved to {report_path}")
    print("\n=== Real Measured Held-out Test Metrics ===")
    print(f"Precision: {metrics['test_metrics']['precision']:.4f}")
    print(f"Recall:    {metrics['test_metrics']['recall']:.4f}")
    print(f"F1 Score:  {metrics['test_metrics']['f1_score']:.4f}")
    print(f"ROC-AUC:   {metrics['test_metrics']['roc_auc']:.4f}")
    print(f"PR-AUC:    {metrics['test_metrics']['pr_auc']:.4f}")
    print(f"Confusion Matrix: {metrics['test_metrics']['confusion_matrix']}")

    return metrics


if __name__ == "__main__":
    train_models()
