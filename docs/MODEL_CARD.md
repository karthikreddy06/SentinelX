# Model Card: SentinelX Hybrid Fraud Detection Model

## Model Overview
- **Model Name**: SentinelX Fraud Classifier (`ml_v1.0.0`)
- **Architecture**: Hybrid Ensemble — Supervised `RandomForestClassifier` (100 trees, max depth 8, balanced class weights) coupled with Unsupervised `IsolationForest` (contamination 0.03).
- **Artifact**: `ml/artifacts/sentinelx_ml_v1.0.0.joblib` (365.5 KB)
- **Metadata**: `ml/artifacts/sentinelx_ml_v1.0.0.json`
- **Report**: `ml/reports/metrics.json`
- **Date**: November 2026

---

## Intended Use
- **Primary Use**: Ingest point-in-time transaction behavior features and produce a continuous ML fraud likelihood score (0 to 100), combined into a hybrid blend with rules-based scoring.
- **Intended Users**: Fraud risk engines, investigators, compliance analysts in real-time banking and payment systems.
- **Out of Scope**: Credit scoring, credit underwriting, or any non-fraud consumer classification.

---

## Data & Synthetic Caveat

> [!WARNING]
> **CRITICAL SYNTHETIC DATA NOTICE**:
> This model is trained and evaluated **exclusively on synthetically generated transactions** produced by `ml/synthetic.py` (seed: 42). All accounts (`ACC_0000` to `ACC_0149`), amounts, device IDs, IP addresses, and injected fraud patterns (stealth account takeovers, card testing, velocity bursts, structuring, impossible travel, mule pass-throughs) are synthetic. Under no circumstances should these metrics be represented as production performance on real-world bank customer traffic without re-training and validation on authentic institution data.

---

## Features (17 Point-in-Time Behavioral Inputs)
1. `current_amount`: Transaction monetary value
2. `velocity_1m_count`: Transactions sent within 1 minute
3. `velocity_1h_count`: Transactions sent within 1 hour
4. `velocity_24h_count`: Transactions sent within 24 hours
5. `velocity_24h_amount`: Total monetary volume sent in 24 hours
6. `avg_amount`: Historical average transaction amount
7. `std_amount`: Historical standard deviation of amount
8. `z_score`: Statistical z-score of current amount
9. `is_first_time_receiver`: Binary indicator of first transfer to recipient
10. `is_new_device`: Binary indicator of unfamiliar device ID
11. `is_new_ip`: Binary indicator of unfamiliar IP address
12. `impossible_travel`: Country change within < 2 hours
13. `is_unusual_hour`: Transfer in early morning hours (1-5 AM) atypical for user
14. `total_structuring_count`: Count of amounts structured just below reporting limit
15. `is_round_number`: Large round amount indicator
16. `recent_failures_1h`: Burst of recent failed/cancelled transactions
17. `pass_through_detected`: Rapid inbound-to-outbound fund routing

---

## Evaluation Metrics (Held-Out Test Set)

Evaluated on chronologically partitioned held-out test data (Train 70%, Validation 15%, Test 15%):

| Metric | Score |
|---|---|
| **Precision** | `0.5417` |
| **Recall** | `0.8667` |
| **F1 Score** | `0.6667` |
| **ROC-AUC** | `0.9764` |
| **PR-AUC (Avg Precision)** | `0.8356` |

### Confusion Matrix
- **True Negatives (TN)**: 272
- **False Positives (FP)**: 11
- **False Negatives (FN)**: 2
- **True Positives (TP)**: 13

---

## Benchmark Comparisons & Ablation

### 1. Rules-Only Baseline (10 Transparent Rules)
- **Precision**: `1.0000`
- **Recall**: `0.1333`
- **F1 Score**: `0.2353`
- *Observation*: The rules-only baseline achieves 100% precision on rigid patterns (e.g. structuring, impossible travel), but misses 86.7% of stealth/burst/card-testing fraud.

### 2. Feature Ablation (Model trained without `current_amount`)
- **Precision**: `0.5417`
- **Recall**: `0.8667`
- **F1 Score**: `0.6667`
- **ROC-AUC**: `0.9651`
- *Observation*: Behavioral features (velocities, deviation z-scores, device/IP novelty, structuring counts) retain 96.5% ROC-AUC without any knowledge of raw transaction amount, confirming the model does not depend on simplistic amount thresholds.

### 3. Precision-Recall Curve Across Thresholds
| Operating Threshold | Precision | Recall | F1 Score |
|---|---|---|---|
| `0.20` | `0.1442` | `1.0000` | `0.2521` |
| `0.30` | `0.2593` | `0.9333` | `0.4058` |
| `0.40` | `0.3889` | `0.9333` | `0.5490` |
| **`0.50` (Chosen)** | **`0.5417`** | **`0.8667`** | **`0.6667`** |
| `0.60` | `0.6667` | `0.8000` | `0.7273` |
| `0.70` | `0.7857` | `0.7333` | `0.7586` |
| `0.80` | `1.0000` | `0.6000` | `0.7500` |

*Rationale for Threshold 0.50*: Balances investigator workload and catches 86.7% of all fraud incidents on class-imbalanced banking streams.

---

## Top Contributing Features
1. `current_amount` (17.75%)
2. `avg_amount` (12.75%)
3. `std_amount` (11.52%)
4. `z_score` (10.28%)
5. `velocity_1h_count` (10.16%)

---

## Limitations & Fallback Protocol
- **Fallback**: If the ML artifact is unavailable, corrupted, or disabled via configuration, SentinelX automatically falls back to 100% rules-based scoring with zero downtime or request degradation.
- **Drift**: Synthetic data does not exhibit real macroeconomic drift or evolving adversarial evasion techniques. Production deployment requires continuous monitoring and retraining.
