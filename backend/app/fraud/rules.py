from abc import ABC, abstractmethod
from typing import Any

from app.models.transaction import Transaction


class RuleResult:
    def __init__(
        self,
        rule_name: str,
        reason_code: str,
        triggered: bool,
        weight: int,
        score_contribution: int,
        description: str,
        evidence: dict[str, Any],
    ) -> None:
        self.rule_name = rule_name
        self.reason_code = reason_code
        self.triggered = triggered
        self.weight = weight
        self.score_contribution = score_contribution
        self.description = description
        self.evidence = evidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_name": self.rule_name,
            "reason_code": self.reason_code,
            "triggered": self.triggered,
            "weight": self.weight,
            "score_contribution": self.score_contribution,
            "description": self.description,
            "evidence": self.evidence,
        }


class BaseRule(ABC):
    name: str
    reason_code: str
    description: str

    @abstractmethod
    def evaluate(
        self,
        transaction: Transaction,
        features: dict[str, Any],
        config: dict[str, Any],
    ) -> RuleResult:
        pass


class VelocityBurstRule(BaseRule):
    name = "velocity_burst"
    reason_code = "VEL_BURST"
    description = "High transaction velocity detected in short time windows (1m/1h/24h)."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        thresholds = config["thresholds"]
        weight = config["weights"]["velocity_burst"]

        v1m = features["velocity_1m_count"]
        v1h = features["velocity_1h_count"]
        v24h = features["velocity_24h_count"]

        triggered = (
            v1m >= thresholds["velocity_1m_max"]
            or v1h >= thresholds["velocity_1h_max"]
            or v24h >= thresholds["velocity_24h_max"]
        )

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={"velocity_1m": v1m, "velocity_1h": v1h, "velocity_24h": v24h},
        )


class AmountAnomalyRule(BaseRule):
    name = "amount_anomaly"
    reason_code = "AMT_ANOMALY"
    description = "Transaction amount is a statistical outlier or exceeds the high-amount ceiling."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        thresholds = config["thresholds"]
        weight = config["weights"]["amount_anomaly"]

        amt = features["current_amount"]
        z_score = features["z_score"]
        hard_limit = thresholds["hard_high_amount"]

        triggered = amt >= hard_limit or z_score >= 3.0

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={
                "amount": amt,
                "z_score": z_score,
                "hard_limit": hard_limit,
                "historical_avg": features["avg_amount"],
            },
        )


class NewReceiverHighAmountRule(BaseRule):
    name = "new_receiver_high_amount"
    reason_code = "NEW_RECV_HIGH_AMT"
    description = "First-time transfer to a new beneficiary with a high or atypical amount."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        thresholds = config["thresholds"]
        weight = config["weights"]["new_receiver_high_amount"]

        is_first_time = features["is_first_time_receiver"]
        amt = features["current_amount"]
        avg_amt = features["avg_amount"]
        min_high = thresholds["new_receiver_high_amount"]

        triggered = is_first_time and (amt >= min_high or (avg_amt > 0 and amt >= 2.5 * avg_amt))

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={
                "is_first_time_receiver": is_first_time,
                "amount": amt,
                "avg_amount": avg_amt,
            },
        )


class UnusualHourRule(BaseRule):
    name = "unusual_hour"
    reason_code = "UNUSUAL_HOUR"
    description = "Transaction occurred during unusual early-morning hours relative to historical habits."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["unusual_hour"]
        triggered = features["is_unusual_hour"]

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={"hour_of_day": features["hour_of_day"]},
        )


class ImpossibleTravelRule(BaseRule):
    name = "impossible_travel"
    reason_code = "IMPOSSIBLE_TRAVEL"
    description = "Consecutive transactions from different geographical countries within impossible travel window."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["impossible_travel"]
        triggered = features["impossible_travel"]

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={
                "country_changed": features["country_changed"],
                "seconds_since_last_tx": features["seconds_since_last_tx"],
            },
        )


class NewDeviceOrIPRule(BaseRule):
    name = "new_device_or_ip"
    reason_code = "NEW_DEVICE_IP"
    description = "Transaction originated from both an unrecognized device and unfamiliar IP address."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["new_device_or_ip"]
        triggered = features["is_new_device"] and features["is_new_ip"]

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={
                "is_new_device": features["is_new_device"],
                "is_new_ip": features["is_new_ip"],
            },
        )


class StructuringRule(BaseRule):
    name = "structuring"
    reason_code = "STRUCTURING"
    description = "Repeated transaction amounts structured just below regulatory reporting thresholds."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["structuring"]
        count = features["total_structuring_count"]
        triggered = count >= 2

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={"structuring_transaction_count_24h": count},
        )


class RoundNumberRule(BaseRule):
    name = "round_number"
    reason_code = "ROUND_NUMBER"
    description = "Significant round-number monetary amount frequently indicative of informal transfers."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["round_number"]
        triggered = features["is_round_number"] and features["current_amount"] >= 1000.0

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={
                "amount": features["current_amount"],
                "is_round": features["is_round_number"],
            },
        )


class PassThroughRule(BaseRule):
    name = "pass_through"
    reason_code = "PASS_THROUGH"
    description = "Rapid in-then-out flow: sender received matching funds shortly before outbound transfer."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        weight = config["weights"]["pass_through"]
        triggered = features["pass_through_detected"]

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={"pass_through_detected": triggered},
        )


class BurstFailuresRule(BaseRule):
    name = "burst_failures"
    reason_code = "BURST_FAILURES"
    description = "Burst of recent failed or cancelled transactions on sender account."

    def evaluate(
        self, transaction: Transaction, features: dict[str, Any], config: dict[str, Any]
    ) -> RuleResult:
        thresholds = config["thresholds"]
        weight = config["weights"]["burst_failures"]
        recent = features["recent_failures_1h"]
        triggered = recent >= thresholds["burst_failures_max"]

        return RuleResult(
            rule_name=self.name,
            reason_code=self.reason_code,
            triggered=triggered,
            weight=weight,
            score_contribution=weight if triggered else 0,
            description=self.description,
            evidence={"recent_failures_1h": recent},
        )


ALL_RULES: list[BaseRule] = [
    VelocityBurstRule(),
    AmountAnomalyRule(),
    NewReceiverHighAmountRule(),
    UnusualHourRule(),
    ImpossibleTravelRule(),
    NewDeviceOrIPRule(),
    StructuringRule(),
    RoundNumberRule(),
    PassThroughRule(),
    BurstFailuresRule(),
]
