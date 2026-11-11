from datetime import datetime, timedelta
from decimal import Decimal
import math
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.transaction import Transaction, TransactionStatus


def extract_point_in_time_features(
    db: Session, transaction: Transaction
) -> dict[str, Any]:
    """
    Extract per-transaction behavioral features using only point-in-time historical data.
    Strictly filters occurred_at < transaction.occurred_at (no data leakage).
    """
    tx_time = transaction.occurred_at
    sender_id = transaction.sender_account_id
    current_amount = float(transaction.amount)

    # 1. Historical sender transactions
    history_query = (
        select(Transaction)
        .where(
            Transaction.sender_account_id == sender_id,
            Transaction.occurred_at < tx_time,
        )
        .order_by(Transaction.occurred_at.desc())
    )
    sender_history = list(db.execute(history_query).scalars().all())

    total_history_count = len(sender_history)
    hist_amounts = [float(t.amount) for t in sender_history]

    # Stats calculation
    if total_history_count > 0:
        avg_amount = sum(hist_amounts) / total_history_count
        variance = sum((x - avg_amount) ** 2 for x in hist_amounts) / total_history_count
        std_amount = math.sqrt(variance) if variance > 0 else 0.0

        sorted_amounts = sorted(hist_amounts)
        mid = total_history_count // 2
        median_amount = (
            (sorted_amounts[mid - 1] + sorted_amounts[mid]) / 2.0
            if total_history_count % 2 == 0
            else sorted_amounts[mid]
        )
        deviations = [abs(x - median_amount) for x in hist_amounts]
        sorted_devs = sorted(deviations)
        mad_amount = (
            (sorted_devs[mid - 1] + sorted_devs[mid]) / 2.0
            if total_history_count % 2 == 0
            else sorted_devs[mid]
        )
    else:
        avg_amount = current_amount
        std_amount = 0.0
        median_amount = current_amount
        mad_amount = 0.0

    z_score = (current_amount - avg_amount) / std_amount if std_amount > 0 else 0.0

    # 2. Velocity Windows
    time_1m = tx_time - timedelta(minutes=1)
    time_1h = tx_time - timedelta(hours=1)
    time_24h = tx_time - timedelta(hours=24)

    vel_1m = sum(1 for t in sender_history if t.occurred_at >= time_1m)
    vel_1h = sum(1 for t in sender_history if t.occurred_at >= time_1h)
    vel_24h = sum(1 for t in sender_history if t.occurred_at >= time_24h)
    amount_24h = sum(float(t.amount) for t in sender_history if t.occurred_at >= time_24h)

    # 3. New Receiver Check
    prev_receivers = {t.receiver_account_id for t in sender_history}
    is_first_time_receiver = transaction.receiver_account_id not in prev_receivers

    # 4. Device and IP history
    prev_devices = {t.device_id for t in sender_history if t.device_id}
    prev_ips = {t.ip_address for t in sender_history if t.ip_address}
    is_new_device = (
        transaction.device_id is not None
        and total_history_count >= 2
        and transaction.device_id not in prev_devices
    )
    is_new_ip = (
        transaction.ip_address is not None
        and total_history_count >= 2
        and transaction.ip_address not in prev_ips
    )

    # 5. Travel & Location Anomaly
    last_tx = sender_history[0] if sender_history else None
    seconds_since_last_tx: float | None = None
    country_changed = False
    impossible_travel = False

    if last_tx and last_tx.country_code and transaction.country_code:
        seconds_since_last_tx = (tx_time - last_tx.occurred_at).total_seconds()
        if last_tx.country_code != transaction.country_code:
            country_changed = True
            # Country change within 2 hours is considered impossible travel
            if seconds_since_last_tx < 7200:
                impossible_travel = True

    # 6. Hour of Day & Unusual Hour
    current_hour = tx_time.hour
    is_night_hour = current_hour in [1, 2, 3, 4]
    if is_night_hour and total_history_count >= 5:
        night_txs = sum(1 for t in sender_history if t.occurred_at.hour in [1, 2, 3, 4])
        is_unusual_hour = (night_txs / total_history_count) < 0.05
    else:
        is_unusual_hour = False

    # 7. Structuring Detection
    def is_in_band(amt: float) -> bool:
        return (9000.0 <= amt <= 9999.99) or (900.0 <= amt <= 999.99)

    structuring_24h = sum(
        1 for t in sender_history if t.occurred_at >= time_24h and is_in_band(float(t.amount))
    )
    current_is_structuring = is_in_band(current_amount)
    total_structuring_count = structuring_24h + (1 if current_is_structuring else 0)

    # 8. Round Number Amount
    is_round_number = (
        current_amount >= 500.0
        and (current_amount % 100.0 == 0.0 or current_amount % 500.0 == 0.0)
    )

    # 9. Recent Failures Burst (failed or cancelled within last 1 hour)
    recent_failures = sum(
        1
        for t in sender_history
        if t.occurred_at >= time_1h
        and t.status in [TransactionStatus.FAILED, TransactionStatus.CANCELLED]
    )

    # 10. Pass-Through (In-then-Out Rapid Flow)
    time_2h = tx_time - timedelta(hours=2)
    inbound_query = select(Transaction).where(
        Transaction.receiver_account_id == sender_id,
        Transaction.occurred_at >= time_2h,
        Transaction.occurred_at < tx_time,
        Transaction.status.in_([TransactionStatus.PENDING, TransactionStatus.COMPLETED]),
    )
    recent_inbound = list(db.execute(inbound_query).scalars().all())
    pass_through_detected = any(
        float(t.amount) >= (current_amount * 0.75) for t in recent_inbound
    )

    return {
        "current_amount": current_amount,
        "total_history_count": total_history_count,
        "avg_amount": round(avg_amount, 2),
        "std_amount": round(std_amount, 2),
        "median_amount": round(median_amount, 2),
        "mad_amount": round(mad_amount, 2),
        "z_score": round(z_score, 2),
        "velocity_1m_count": vel_1m,
        "velocity_1h_count": vel_1h,
        "velocity_24h_count": vel_24h,
        "velocity_24h_amount": round(amount_24h, 2),
        "is_first_time_receiver": is_first_time_receiver,
        "is_new_device": is_new_device,
        "is_new_ip": is_new_ip,
        "seconds_since_last_tx": (
            round(seconds_since_last_tx, 1) if seconds_since_last_tx is not None else None
        ),
        "country_changed": country_changed,
        "impossible_travel": impossible_travel,
        "hour_of_day": current_hour,
        "is_unusual_hour": is_unusual_hour,
        "total_structuring_count": total_structuring_count,
        "current_is_structuring": current_is_structuring,
        "is_round_number": is_round_number,
        "recent_failures_1h": recent_failures,
        "pass_through_detected": pass_through_detected,
    }
