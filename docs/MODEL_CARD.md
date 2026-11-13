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

Evaluated on chronologically partitioned held-out test data (Train 70% [n=1390], Validation 15% [n=298], Test 15% [n=298]):
- **Test Set Size**: 298 transactions
- **Fraud Prevalence (Ground Truth)**: `5.03%` (15 fraud cases, 283 legitimate cases)
- **Label Noise Injection**: `1.0%` random label flipping (realistic real-world noise)
- **Temporal Leakage Audit**: PASSED (`max(train_ts) <= min(val_ts) <= min(test_ts)`)

### Side-by-Side Model Comparison on Held-Out Test Set

| Approach | Precision | Recall | F1 Score | ROC-AUC | PR-AUC | Confusion Matrix (TN / FP / FN / TP) |
|---|---|---|---|---|---|---|
| **Rules-Only Baseline** (10 rules) | `1.0000` | `0.1333` | `0.2353` | `0.7416` | `0.2962` | `283 / 0 / 13 / 2` |
| **Ablation** (ML without `current_amount`) | `0.5417` | `0.8667` | `0.6667` | `0.9651` | `0.8431` | `272 / 11 / 2 / 13` |
| **Hybrid Blend** (60% Rules + 40% ML) | `0.8333` | `0.3333` | `0.4762` | `0.9635` | `0.7715` | `282 / 1 / 10 / 5` |
| **ML-Only (RandomForest)** | `0.5417` | `0.8667` | `0.6667` | `0.9764` | `0.8356` | `272 / 11 / 2 / 13` |

### Plain Evaluation: Does ML Beat Rules?
**Yes, ML decisively beats the rules-only baseline for fraud detection.**
- **The Failure of Rules-Only**: While the rules-only baseline achieves 100% precision on rigid patterns (impossible travel, exact structuring thresholds), it suffers from an abysmal **13.33% recall** — missing 13 out of 15 fraud incidents (86.7% fraud missed).
- **The Strength of ML**: The supervised ML model catches **86.67% of all fraud incidents** (13 out of 15 caught) while maintaining 54.17% precision and 0.8356 PR-AUC.
- **Independence from Raw Amount**: The feature ablation model (trained without `current_amount`) achieves identical recall (86.67%) and 0.9651 ROC-AUC, proving that ML learns non-linear behavioral relationships (velocity bursts, z-score deviations, rapid counterparty hops) rather than overfitting to large transaction amounts.
- **The Role of the Hybrid Model**: In production, the Hybrid model provides a configurable knob: when investigator bandwidth is constrained, raising the threshold or weighting rules reduces false positives (Hybrid yields 83.33% precision with only 1 false alarm).

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

## Limitations & Explicit Disclaimers

### What These Metrics Do NOT Prove
1. **Adversarial Realism**: Synthetic fraud patterns are generated according to parameterized distributions. They do **not** reflect real-world adversarial evasion where human fraudsters actively probe and adapt to model decision boundaries.
2. **Production Banking Generalization**: High ROC-AUC on synthetic held-out splits does **not** guarantee equivalent performance on authentic production transactions, heterogeneous customer demographics, or multi-currency payment networks.
3. **Ground-Truth Label Latency**: Real-world fraud labels typically suffer from 30- to 90-day reporting delays (chargebacks, claims). In synthetic training, ground truth is instantaneously available at generation time.
4. **False Positive Business Impact**: A 45.8% false alarm rate (precision 0.5417) on a 5% fraud stream represents 11 false positives per 24 flagged transactions. In high-volume production payment processing, this would require careful calibration to prevent customer checkout abandonment.

### Fallback Protocol & Continuous Monitoring
- **Automatic Rules Fallback**: If the ML artifact is unavailable, corrupted, or disabled via `FRAUD_SCORING_ENABLED=False`, SentinelX automatically falls back to 100% deterministic rules-based scoring with zero downtime or request failure.
- **Model Drift Detection**: Celery Beat schedules daily distribution divergence checks (`check_model_drift_task`) monitoring feature drift against the baseline artifact.
