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
    num_accounts: int = 150,
    days: int = 21,
    seed: int = 42,
    fraud_rate: float = 0.02,
) -> list[dict[str, Any]]:
    """
    Generate realistic synthetic transaction sequences with overlapping legitimate
    and fraudulent distributions, multi-modal amounts, stealth attacks, missing fields,
    and realistic label noise.

    Strictly deterministic when seeded.
    """
    rng = random.Random(seed)
    start_time = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    accounts = [f"ACC_{i:04d}" for i in range(num_accounts)]

    # Account profiles: typical behavior baselines
    profiles: dict[str, dict[str, Any]] = {}
    for acc in accounts:
        # Base spending tier: student/low (40%), middle income (50%), affluent/business (10%)
        tier_roll = rng.random()
        if tier_roll < 0.40:
            spending_tier = "low"
            base_mean = rng.uniform(25.0, 75.0)
        elif tier_roll < 0.90:
            spending_tier = "medium"
            base_mean = rng.uniform(80.0, 250.0)
        else:
            spending_tier = "high"
            base_mean = rng.uniform(300.0, 1200.0)

        profiles[acc] = {
            "tier": spending_tier,
            "mean_amount": base_mean,
            "country": "US" if rng.random() < 0.85 else rng.choice(["CA", "GB", "DE", "FR"]),
            "device": f"dev_{acc}",
            "ip": f"192.168.{rng.randint(1, 20)}.{rng.randint(2, 250)}",
            "active_receivers": [rng.choice(accounts) for _ in range(rng.randint(2, 6))],
            "channel": rng.choice(["web", "mobile", "pos"]),
        }

    transactions: list[dict[str, Any]] = []

    # 1. Generate Multi-Modal Legitimate Traffic
    total_hours = days * 24
    for hour in range(total_hours):
        current_hour_time = start_time + timedelta(hours=hour)
        hour_of_day = current_hour_time.hour

        # Diurnal seasonality: low at night (0.08), peak midday/evening (0.9 - 1.0)
        if 1 <= hour_of_day <= 5:
            activity_multiplier = 0.08
        elif 9 <= hour_of_day <= 20:
            activity_multiplier = 1.0
        else:
            activity_multiplier = 0.45

        active_count = int(num_accounts * 0.04 * activity_multiplier)
        chosen_senders = rng.sample(accounts, max(1, min(active_count, len(accounts))))

        for sender in chosen_senders:
            prof = profiles[sender]

            # Multi-modal amount generation
            amt_type_roll = rng.random()
            if amt_type_roll < 0.70:
                # Small retail / dining / everyday: $5 - $150
                amt = max(5.0, rng.expovariate(1.0 / max(15.0, prof["mean_amount"] * 0.5)))
                amt = min(amt, 150.0)
            elif amt_type_roll < 0.95:
                # Utility / electronics / subscriptions / shopping: $150 - $1,200
                amt = rng.uniform(150.0, 1200.0)
            else:
                # Legitimate large transfer (rent, B2B, tuition, contractor): $1,200 - $8,000
                amt = rng.uniform(1200.0, 8000.0)

            receiver = rng.choice(prof["active_receivers"])
            if receiver == sender:
                receiver = rng.choice([a for a in accounts if a != sender])

            # Occasional new device / IP for legitimate users (e.g. hotel, work, new phone)
            device_id = prof["device"]
            if rng.random() < 0.04:
                device_id = f"work_dev_{sender}"
            elif rng.random() < 0.03:
                device_id = None  # Missing device_id (~3%)

            ip_address = prof["ip"]
            if rng.random() < 0.05:
                ip_address = f"10.0.{rng.randint(1, 10)}.{rng.randint(2, 250)}"
            elif rng.random() < 0.03:
                ip_address = None  # Missing ip (~3%)

            minute_offset = rng.randint(0, 59)
            second_offset = rng.randint(0, 59)
            tx_time = current_hour_time + timedelta(minutes=minute_offset, seconds=second_offset)

            channel = prof["channel"] if rng.random() > 0.03 else None

            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(amt, 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": tx_time,
                    "country_code": prof["country"],
                    "device_id": device_id,
                    "ip_address": ip_address,
                    "channel": channel,
                    "status": "completed",
                    "is_fraud": 0,
                    "fraud_scenario": "legitimate",
                }
            )

    # 2. Inject Fraud Patterns
    num_fraud = max(10, int(len(transactions) * fraud_rate))
    fraud_scenarios = [
        "account_takeover_stealth",
        "card_testing",
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

        if scenario == "account_takeover_stealth":
            # Stealth ATO: Normal-range amount ($80 - $450) but from new device & foreign/proxy IP
            fraud_tx = {
                "sender_account_id": sender,
                "receiver_account_id": receiver,
                "amount": round(rng.uniform(80.0, 450.0), 2),
                "currency": "USD",
                "transaction_type": "transfer",
                "occurred_at": target_time,
                "country_code": rng.choice(["RU", "NG", "CN", "BR"]),
                "device_id": f"stealth_hacker_dev_{f_idx}",
                "ip_address": f"198.51.100.{rng.randint(2, 250)}",
                "channel": "web",
                "status": "completed",
                "is_fraud": 1,
                "fraud_scenario": scenario,
            }
            transactions.append(fraud_tx)

        elif scenario == "card_testing":
            # Low amount ($5 - $45) rapid micro-testing
            for ct_i in range(3):
                ct_time = target_time + timedelta(seconds=ct_i * 15)
                transactions.append(
                    {
                        "sender_account_id": sender,
                        "receiver_account_id": rng.choice([a for a in accounts if a != sender]),
                        "amount": round(rng.uniform(5.0, 45.0), 2),
                        "currency": "USD",
                        "transaction_type": "transfer",
                        "occurred_at": ct_time,
                        "country_code": profiles[sender]["country"],
                        "device_id": f"card_tester_dev_{f_idx}",
                        "ip_address": f"203.0.113.{rng.randint(2, 250)}",
                        "channel": "pos",
                        "status": "failed" if ct_i < 2 else "completed",
                        "is_fraud": 1,
                        "fraud_scenario": scenario,
                    }
                )

        elif scenario == "velocity_burst":
            # 4-5 transactions in 1 minute with moderate amounts ($120 - $650)
            burst_len = rng.randint(4, 5)
            for v_i in range(burst_len):
                v_time = target_time + timedelta(seconds=v_i * 12)
                transactions.append(
                    {
                        "sender_account_id": sender,
                        "receiver_account_id": rng.choice([a for a in accounts if a != sender]),
                        "amount": round(rng.uniform(120.0, 650.0), 2),
                        "currency": "USD",
                        "transaction_type": "transfer",
                        "occurred_at": v_time,
                        "country_code": profiles[sender]["country"],
                        "device_id": profiles[sender]["device"],
                        "ip_address": profiles[sender]["ip"],
                        "channel": "mobile",
                        "status": "completed",
                        "is_fraud": 1,
                        "fraud_scenario": scenario,
                    }
                )

        elif scenario == "structuring":
            # Amounts just below reporting limit ($9,200 - $9,950 or $910 - $990)
            struct_amt = rng.choice([rng.uniform(9200.0, 9950.0), rng.uniform(910.0, 990.0)])
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(struct_amt, 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": profiles[sender]["device"],
                    "ip_address": profiles[sender]["ip"],
                    "channel": "web",
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

        elif scenario == "impossible_travel":
            # Transaction 15 minutes later from another continent
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": round(rng.uniform(200.0, 1500.0), 2),
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time + timedelta(minutes=15),
                    "country_code": "SG" if profiles[sender]["country"] != "SG" else "DE",
                    "device_id": profiles[sender]["device"],
                    "ip_address": f"203.0.113.{rng.randint(2, 250)}",
                    "channel": "web",
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

        elif scenario == "mule_passthrough":
            # Inbound transaction followed quickly by outbound ($1,500 - $4,500)
            inbound_amt = round(rng.uniform(2000.0, 5000.0), 2)
            outbound_amt = round(inbound_amt * rng.uniform(0.92, 0.98), 2)
            inbound_time = target_time - timedelta(minutes=20)

            transactions.append(
                {
                    "sender_account_id": f"external_victim_{f_idx}",
                    "receiver_account_id": sender,
                    "amount": inbound_amt,
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": inbound_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": f"ext_dev_{f_idx}",
                    "ip_address": "172.16.0.8",
                    "channel": "web",
                    "status": "completed",
                    "is_fraud": 0,
                    "fraud_scenario": "mule_inbound",
                }
            )
            transactions.append(
                {
                    "sender_account_id": sender,
                    "receiver_account_id": receiver,
                    "amount": outbound_amt,
                    "currency": "USD",
                    "transaction_type": "transfer",
                    "occurred_at": target_time,
                    "country_code": profiles[sender]["country"],
                    "device_id": profiles[sender]["device"],
                    "ip_address": profiles[sender]["ip"],
                    "channel": "web",
                    "status": "completed",
                    "is_fraud": 1,
                    "fraud_scenario": scenario,
                }
            )

    # 3. Add Realistic Label Noise (1.0% random label flipping to emulate real-world noise)
    for tx in transactions:
        if tx["fraud_scenario"] != "mule_inbound" and rng.random() < 0.01:
            tx["is_fraud"] = 1 if tx["is_fraud"] == 0 else 0

    # Sort strictly chronologically by occurred_at
    transactions.sort(key=lambda x: x["occurred_at"])
    return transactions
