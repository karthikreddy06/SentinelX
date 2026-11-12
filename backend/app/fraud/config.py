from typing import Any

DEFAULT_RULES_CONFIG: dict[str, Any] = {
    "engine_version": "rules_v1.0.0",
    "thresholds": {
        "low_max": 29,
        "medium_max": 69,
        "high_min": 70,
        "hard_high_amount": 10000.0,
        "new_receiver_high_amount": 1000.0,
        "impossible_travel_max_seconds": 7200,  # 2 hours
        "structuring_lower": 9000.0,
        "structuring_upper": 9999.99,
        "structuring_lower_small": 900.0,
        "structuring_upper_small": 999.99,
        "velocity_1m_max": 3,
        "velocity_1h_max": 8,
        "velocity_24h_max": 20,
        "burst_failures_max": 2,
    },
    "weights": {
        "velocity_burst": 25,
        "amount_anomaly": 30,
        "new_receiver_high_amount": 20,
        "unusual_hour": 15,
        "impossible_travel": 35,
        "new_device_or_ip": 15,
        "structuring": 25,
        "round_number": 10,
        "pass_through": 25,
        "burst_failures": 20,
        "network_risk": 35,
    },
}
