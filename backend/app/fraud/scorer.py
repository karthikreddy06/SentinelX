from typing import Any

from app.fraud.config import DEFAULT_RULES_CONFIG
from app.fraud.rules import ALL_RULES, BaseRule
from app.ml.predictor import predict_fraud_score
from app.models.transaction import RiskLevel, Transaction


class RulesEngine:
    def __init__(
        self,
        rules: list[BaseRule] | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.rules = rules or ALL_RULES
        self.config = config or DEFAULT_RULES_CONFIG

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any]
    ) -> tuple[int, RiskLevel, list[dict[str, Any]], str]:
        """
        Evaluate transaction using a hybrid blend of rules-based scoring and machine learning.
        Returns: (final_score [0-100], risk_level, list_of_triggered_reasons, engine_version)
        """
        raw_rules_score = 0
        triggered_reasons: list[dict[str, Any]] = []

        # 1. Rules-based evaluation
        for rule in self.rules:
            result = rule.evaluate(transaction, features, self.config)
            if result.triggered:
                raw_rules_score += result.score_contribution
                triggered_reasons.append(result.to_dict())

        rules_score = min(100, max(0, raw_rules_score))

        # 2. Machine Learning scoring with graceful fallback
        ml_score, top_ml_features, explanation_method = predict_fraud_score(features)

        if ml_score is not None:
            rules_weight = 0.6
            ml_weight = 0.4
            final_score = int(round(rules_weight * rules_score + ml_weight * ml_score))
            engine_version = f"{self.config['engine_version']}+ml_v1.0.0"

            triggered_reasons.append({
                "rule_name": "ml_anomaly_classifier",
                "reason_code": "ML_CLASSIFIER",
                "triggered": ml_score >= 50,
                "weight": int(ml_weight * 100),
                "score_contribution": int(ml_weight * ml_score),
                "description": f"Supervised ML model estimated {ml_score}% fraud probability.",
                "evidence": {
                    "ml_score": ml_score,
                    "explanation_method": explanation_method,
                    "top_contributing_features": top_ml_features,
                },
            })
        else:
            final_score = rules_score
            engine_version = self.config["engine_version"]

        # Clamp final score between 0 and 100
        final_score = min(100, max(0, final_score))

        # Determine risk level based on configured thresholds
        thresholds = self.config["thresholds"]
        if final_score <= thresholds["low_max"]:
            level = RiskLevel.LOW
        elif final_score <= thresholds["medium_max"]:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.HIGH

        return final_score, level, triggered_reasons, engine_version


default_rules_engine = RulesEngine()
