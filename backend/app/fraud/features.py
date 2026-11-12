from datetime import datetime, timedelta
import math
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.fraud.cache import (
    get_sender_profile,
    get_transaction_velocity,
    record_transaction_velocity,
    set_sender_profile,
)
from app.models.network import AccountNetworkRisk, NetworkRiskFlag
from app.models.transaction import Transaction, TransactionStatus


def extract_point_in_time_features(
    db: Session, transaction: Transaction
) -> dict[str, Any]:
    """
    Extract per-transaction behavioral features using only point-in-time historical data.
    Strictly filters occurred_at < transaction.occurred_at (no data leakage).
    Uses Redis caching for hot-path sender profiles and velocity windows with graceful DB fallback.
    """
    tx_time = transaction.occurred_at
    sender_id = transaction.sender_account_id
    current_amount = float(transaction.amount)
    tx_ts = tx_time.timestamp()

    # 1. Historical sender transactions / Profile Cache
    cached_profile = get_sender_profile(sender_id)
    sender_history: list[Transaction] = []

    # Check if cached profile is applicable (must be strictly point-in-time prior to this transaction)
    use_cached_profile = False
    if cached_profile is not None:
        last_iso = cached_profile.get("last_tx_occurred_at")
        if last_iso:
            last_dt = datetime.fromisoformat(last_iso)
            if last_dt < tx_time:
                use_cached_profile = True
        elif cached_profile.get("total_history_count") == 0:
            use_cached_profile = True

    if use_cached_profile and cached_profile:
        total_history_count = cached_profile["total_history_count"]
        avg_amount = cached_profile["avg_amount"]
        std_amount = cached_profile["std_amount"]
        median_amount = cached_profile["median_amount"]
        mad_amount = cached_profile["mad_amount"]
        prev_receivers = set(cached_profile.get("prev_receivers", []))
        prev_devices = set(cached_profile.get("prev_devices", []))
        prev_ips = set(cached_profile.get("prev_ips", []))
        last_country = cached_profile.get("last_tx_country")
        last_time_iso = cached_profile.get("last_tx_occurred_at")
        last_tx_time = datetime.fromisoformat(last_time_iso) if last_time_iso else None
    else:
        # DB Query for point-in-time sender history
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

        prev_receivers = {t.receiver_account_id for t in sender_history}
        prev_devices = {t.device_id for t in sender_history if t.device_id}
        prev_ips = {t.ip_address for t in sender_history if t.ip_address}
        last_tx = sender_history[0] if sender_history else None
        last_country = last_tx.country_code if last_tx else None
        last_tx_time = last_tx.occurred_at if last_tx else None
        night_tx_count = sum(1 for t in sender_history if t.occurred_at.hour in [1, 2, 3, 4])

        # Populate Redis cache with computed profile
        set_sender_profile(
            sender_id,
            {
                "total_history_count": total_history_count,
                "avg_amount": avg_amount,
                "std_amount": std_amount,
                "median_amount": median_amount,
                "mad_amount": mad_amount,
                "prev_receivers": list(prev_receivers),
                "prev_devices": list(prev_devices),
                "prev_ips": list(prev_ips),
                "last_tx_country": last_country,
                "last_tx_occurred_at": last_tx_time.isoformat() if last_tx_time else None,
                "night_tx_count": night_tx_count,
            },
        )

    z_score = (current_amount - avg_amount) / std_amount if std_amount > 0 else 0.0

    # 2. Velocity Windows
    time_1m = tx_time - timedelta(minutes=1)
    time_1h = tx_time - timedelta(hours=1)
    time_24h = tx_time - timedelta(hours=24)

    cached_vel = get_transaction_velocity(sender_id, tx_ts)
    if cached_vel is not None:
        vel_1m = cached_vel["vel_1m"]
        vel_1h = cached_vel["vel_1h"]
        vel_24h = cached_vel["vel_24h"]
        amount_24h = cached_vel["amount_24h"]
    else:
        if not sender_history:
            history_query = (
                select(Transaction)
                .where(
                    Transaction.sender_account_id == sender_id,
                    Transaction.occurred_at < tx_time,
                )
                .order_by(Transaction.occurred_at.desc())
            )
            sender_history = list(db.execute(history_query).scalars().all())

        vel_1m = sum(1 for t in sender_history if t.occurred_at >= time_1m)
        vel_1h = sum(1 for t in sender_history if t.occurred_at >= time_1h)
        vel_24h = sum(1 for t in sender_history if t.occurred_at >= time_24h)
        amount_24h = sum(float(t.amount) for t in sender_history if t.occurred_at >= time_24h)

        # Seed Redis velocity cache with historical transactions
        for t in sender_history:
            if t.occurred_at >= time_24h:
                record_transaction_velocity(sender_id, float(t.amount), t.occurred_at.timestamp())

    # Record current transaction in velocity cache
    record_transaction_velocity(sender_id, current_amount, tx_ts)

    # 3. New Receiver Check
    is_first_time_receiver = transaction.receiver_account_id not in prev_receivers

    # 4. Device and IP history
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
    seconds_since_last_tx: float | None = None
    country_changed = False
    impossible_travel = False

    if last_country and transaction.country_code and last_tx_time:
        seconds_since_last_tx = (tx_time - last_tx_time).total_seconds()
        if last_country != transaction.country_code:
            country_changed = True
            # Country change within 2 hours is considered impossible travel
            if seconds_since_last_tx < 7200:
                impossible_travel = True

    # 6. Hour of Day & Unusual Hour
    current_hour = tx_time.hour
    is_night_hour = current_hour in [1, 2, 3, 4]
    if is_night_hour and total_history_count >= 5:
        if use_cached_profile and cached_profile:
            night_txs = cached_profile.get("night_tx_count", 0)
        else:
            if not sender_history:
                history_query = (
                    select(Transaction)
                    .where(
                        Transaction.sender_account_id == sender_id,
                        Transaction.occurred_at < tx_time,
                    )
                    .order_by(Transaction.occurred_at.desc())
                )
                sender_history = list(db.execute(history_query).scalars().all())
            night_txs = sum(1 for t in sender_history if t.occurred_at.hour in [1, 2, 3, 4])
        is_unusual_hour = (night_txs / total_history_count) < 0.05
    else:
        is_unusual_hour = False

    # 7. Structuring Detection & Recent Failures (Efficient 24h index slice)
    def is_in_band(amt: float) -> bool:
        return (9000.0 <= amt <= 9999.99) or (900.0 <= amt <= 999.99)

    # If sender_history was already queried, reuse it; otherwise query only the recent 24h window
    if sender_history:
        active_recent_24h = [t for t in sender_history if t.occurred_at >= time_24h]
    else:
        recent_24h_query = select(Transaction).where(
            Transaction.sender_account_id == sender_id,
            Transaction.occurred_at >= time_24h,
            Transaction.occurred_at < tx_time,
        )
        active_recent_24h = list(db.execute(recent_24h_query).scalars().all())

    structuring_24h = sum(
        1 for t in active_recent_24h if is_in_band(float(t.amount))
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
        for t in active_recent_24h
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

    # 11. Account Network Risk Features
    sender_net = db.execute(
        select(AccountNetworkRisk).where(AccountNetworkRisk.account_id == sender_id)
    ).scalar_one_or_none()
    receiver_net = db.execute(
        select(AccountNetworkRisk).where(
            AccountNetworkRisk.account_id == transaction.receiver_account_id
        )
    ).scalar_one_or_none()

    sender_is_mule = sender_net.is_mule_suspect if sender_net else False
    receiver_is_mule = receiver_net.is_mule_suspect if receiver_net else False
    network_mule_detected = sender_is_mule or receiver_is_mule

    hub_flags = {
        NetworkRiskFlag.FAN_IN_AGGREGATOR,
        NetworkRiskFlag.FAN_OUT_DISTRIBUTOR,
        NetworkRiskFlag.HIGH_CENTRALITY_HUB,
    }
    sender_hub = (sender_net.risk_flag in hub_flags) if sender_net else False
    receiver_hub = (receiver_net.risk_flag in hub_flags) if receiver_net else False
    network_hub_detected = sender_hub or receiver_hub

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
        "network_mule_detected": network_mule_detected,
        "network_hub_detected": network_hub_detected,
        "sender_network_flag": sender_net.risk_flag.value if sender_net else "normal",
        "receiver_network_flag": receiver_net.risk_flag.value if receiver_net else "normal",
    }
