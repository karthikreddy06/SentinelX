# Model Card: SentinelX Hybrid Fraud Detection Model

## Model Overview
- **Model Name**: SentinelX Fraud Classifier (`ml_v1.0.0`)
- **Architecture**: Hybrid Ensemble — Supervised `RandomForestClassifier` (100 trees, max depth 8, balanced class weights) coupled with Unsupervised `IsolationForest` (contamination 0.05).
- **Artifact**: `ml/artifacts/sentinelx_ml_v1.0.0.joblib` (203.8 KB)
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
> This model is trained and evaluated **exclusively on synthetically generated transactions** produced by `ml/synthetic.py` (seed: 42). All accounts (`ACC_0000` to `ACC_0099`), amounts, device IDs, IP addresses, and injected fraud patterns (account takeovers, velocity bursts, structuring, impossible travel, mule pass-throughs) are synthetic. Under no circumstances should these metrics be represented as production performance on real-world bank customer traffic without re-training and validation on authentic institution data.

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
| **Precision** | `1.0000` |
| **Recall** | `0.9375` |
| **F1 Score** | `0.9677` |
| **ROC-AUC** | `0.9994` |
| **PR-AUC (Avg Precision)** | `0.9931` |

### Confusion Matrix
- **True Negatives (TN)**: 217
- **False Positives (FP)**: 0
- **False Negatives (FN)**: 1
- **True Positives (TP)**: 15

---

## Top Contributing Features
1. `z_score` (Statistical deviation from account baseline)
2. `current_amount` (Monetary scale)
3. `velocity_1h_count` (Short-term transaction burst)
4. `impossible_travel` (Cross-border geographical conflict)
5. `total_structuring_count` (Regulatory threshold evasion)

---

## Limitations & Fallback Protocol
- **Fallback**: If the ML artifact is unavailable, corrupted, or disabled via configuration, SentinelX automatically falls back to 100% rules-based scoring with zero downtime or request degradation.
- **Drift**: Synthetic data does not exhibit real macroeconomic drift or evolving adversarial evasion techniques. Production deployment requires continuous monitoring and retraining.
