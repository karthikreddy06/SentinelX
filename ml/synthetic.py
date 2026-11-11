"""
SentinelX Synthetic Fraud Dataset Generator.

IMPORTANT CAVEAT:
This module generates SYNTHETIC financial transaction data solely for development,
benchmarking, and model training in SentinelX. All accounts, patterns, and transactions
are synthetically generated and do not represent real individuals or institutions.
"""

from datetime import datetime, timedelta, timezone
import random
from typing import Any

FEATURE_COLUMNS = [
    "current_amount",
    "velocity_1m_count",
    "velocity_1h_count",
    "velocity_24h_count",
    "velocity_24h_amount",
    "avg_amount",
    "std_amount",
    "z_score",
    "is_first_time_receiver",
    "is_new_device",
    "is_new_ip",
    "impossible_travel",
    "is_unusual_hour",
    "total_structuring_count",
    "is_round_number",
    "recent_failures_1h",
    "pass_through_detected",
]


def generate_synthetic_transactions(
    num_accounts: int = 100,
    days: int = 14,
    seed: int = 42,
    fraud_rate: float = 0.05,
) -> list[dict[str, Any]]:
    """
    Generate realistic synthetic transaction sequences with injected fraud patterns.
    Seeded for strict determinism and reproducibility.
    """
    rng = random.Random(seed)
    start_time = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    accounts = [f"ACC_{i:04d}" for i in range(num_accounts)]

    # Account profiles: typical amount mean, std, preferred country, preferred device
    profiles: dict[str, dict[str, Any]] = {}
    for acc in accounts:
        profiles[acc] = {
            "mean_amount": rng.uniform(30.0, 300.0),
            "country": "US" if rng.random() < 0.85 else rng.choice(["CA", "GB", "DE"]),
            "device": f"dev_{acc}",
            "ip": f"192.168.1.{rng.randint(2, 250)}",
            "active_receivers": [rng.choice(accounts) for _ in range(3)],
        }

    transactions: list[dict[str, Any]] = []

    # 1. Generate Legitimate Traffic
    total_hours = days * 24
    for hour in range(total_hours):
        current_hour_time = start_time + timedelta(hours=hour)
        hour_of_day = current_hour_time.hour
        # Diurnal seasonality: low at night (0.1 weight), high during afternoon (1.0 weight)
        if 1 <= hour_of_day <= 5:
            activity_multiplier = 0.1
        elif 9 <= hour_of_day <= 18:
            activity_multiplier = 1.0
        else:
            activity_multiplier = 0.5

        active_count = int(num_accounts * 0.05 * activity_multiplier)
        chosen_senders = rng.sample(accounts, max(1, active_count))

        for sender in chosen_senders:
            prof = profiles[sender]
            amt = max(5.0, rng.gauss(prof["mean_amount"], prof["mean_amount"] * 0.25))
            receiver = rng.choice(prof["active_receivers"])
            if receiver == sender:
                receiver = rng.choice([a for a in accounts if a != sender])

            minute_offset = rng.randint(0, 59)
            second_offset = rng.randint(0, 59)
            tx_time = current_hour_time + timedelta(minutes=minute_offset, seconds=second_offset)

            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(amt, 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": tx_time,
                    "country_code": prof["country"],
                    "device_id": prof["device"],
                    "ip_address": prof["ip"],
                    "status": "completed",
                    "is_fraud": 0,
                    "fraud_scenario": "legitimate",
                }
            )

    # 2. Inject Fraud Patterns
    num_fraud = int(len(transactions) * fraud_rate)
    fraud_scenarios = [
        "account_takeover",
        "velocity_burst",
        "structuring",
        "impossible_travel",
        "mule_passthrough",
    ]

    for f_idx in range(num_fraud):
        scenario = rng.choice(fraud_scenarios)
        base_tx = rng.choice(transactions)
        target_time = base_tx["occurred_at"] + timedelta(seconds=rng.randint(30, 300))
        sender = rng.choice(accounts)
        receiver = rng.choice([a for a in accounts if a != sender])

        if scenario == "account_takeover":
            # New device, new IP, very high amount
            fraud_tx = {
                "sender_account_id": sender,
                "receiver_account_id": receiver,
                "amount": round(rng.uniform(5000.0, 15000.0), 2),
                "currency": "USD",
                "transaction_type": "transfer",
                "occurred_at": target_time,
                "country_code": "RU" if rng.random() < 0.5 else "NG",
                "device_id": f"hacker_dev_{f_idx}",
                "ip_address": f"10.99.88.{rng.randint(2, 250)}",
                "status": "completed",
                "is_fraud": 1,
                "fraud_scenario": scenario,
            }
            transactions.append(fraud_tx)

        elif scenario == "velocity_burst":
            # 5 transactions in 1 minute
            for v_i in range(4):
                v_time = target_time + timedelta(seconds=v_i * 12)
                transactions.append(
                    {
                        "sender_account_id": sender,
                        "receiver_account_id": rng.choice([a for a in accounts if a != sender]),
                        "amount": round(rng.uniform(300.0, 900.0), 2),
                        "currency": "USD",
                        "transaction_type": "transfer",
                        "occurred_at": v_time,
                        "country_code": profiles[sender]["country"],
                        "device_id": profiles[sender]["device"],
                        "ip_address": profiles[sender]["ip"],
                        "status": "completed",
                        "is_fraud": 1,
                        "fraud_scenario": scenario,
                    }
                )

        elif scenario == "structuring":
            # Amounts just below $10,000 threshold ($9,500 - $9,900)
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(rng.uniform(9200.0, 9950.0), 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": profiles[sender]["device"],
                    "ip_address": profiles[sender]["ip"],
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

        elif scenario == "impossible_travel":
            # Transaction 10 minutes later from another continent
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(rng.uniform(200.0, 1500.0), 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time + timedelta(minutes=15),
                    "country_code": "SG",  # Singapore
                    "device_id": profiles[sender]["device"],
                    "ip_address": "203.0.113.42",
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

        elif scenario == "mule_passthrough":
            # Inbound transaction followed quickly by outbound
            inbound_time = target_time - timedelta(minutes=25)
            transactions.append(
                {
                    "sender_account_id": "victim_ext",
                    "receiver_account_id": sender,
                    "amount": 4000.00,
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": inbound_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": "victim_device",
                    "ip_address": "172.16.0.5",
                    "status": "completed",
                    "is_fraud": 0,
                    "fraud_scenario": "mule_inbound",
                }
            )
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": 3950.00,
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": profiles[sender]["device"],
                    "ip_address": profiles[sender]["ip"],
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

    # Sort strictly chronologically by occurred_at
    transactions.sort(key=lambda x: x["occurred_at"])
    return transactions
