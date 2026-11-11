from typing import Any

from app.fraud.config import DEFAULT_RULES_CONFIG
from app.fraud.rules import ALL_RULES, BaseRule
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
    ) -> tuple[int, RiskLevel, list[dict[str, Any]]]:
        """
        Evaluate all active rules on a transaction.
        Returns: (final_score [0-100], risk_level, list_of_triggered_reasons)
        """
        raw_score = 0
        triggered_reasons: list[dict[str, Any]] = []

        for rule in self.rules:
            result = rule.evaluate(transaction, features, self.config)
            if result.triggered:
                raw_score += result.score_contribution
                triggered_reasons.append(result.to_dict())

        # Clamp score between 0 and 100
        final_score = min(100, max(0, raw_score))

        # Determine risk level based on configured thresholds
        thresholds = self.config["thresholds"]
        if final_score <= thresholds["low_max"]:
            level = RiskLevel.LOW
        elif final_score <= thresholds["medium_max"]:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.HIGH

        return final_score, level, triggered_reasons


default_rules_engine = RulesEngine()
