from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
import random
import sys
import uuid

# Add backend directory to sys.path
backend_dir = Path(__file__).resolve().parents[1]
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from sqlalchemy import select
from app.core.security import hash_password
from app.database.connection import SessionLocal
from app.fraud.features import extract_point_in_time_features
from app.fraud.scorer import RulesEngine
from app.models.alert import Alert, AlertStatus
from app.models.fraud_score import FraudScore
from app.models.investigation import (
    EvidenceType,
    Investigation,
    InvestigationEvidence,
    InvestigationNote,
    InvestigationPriority,
    InvestigationStatus,
)
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import User, UserRole
from app.services import alert_service, network_service


def seed_users(db) -> dict[str, User]:
    users_data = [
        ("admin@sentinelx.local", "AdminSecret2026!", UserRole.ADMIN, "Lead Administrator"),
        ("investigator@sentinelx.local", "Investigator2026!", UserRole.INVESTIGATOR, "Fraud Investigator Sarah"),
        ("analyst@sentinelx.local", "Analyst2026!", UserRole.ANALYST, "Triage Analyst Dave"),
    ]
    users_map = {}
    for email, password, role, name in users_data:
        user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if not user:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                role=role,
                full_name=name,
                is_active=True,
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        users_map[role.value] = user
    return users_map


def seed_transactions(db) -> list[Transaction]:
    now = datetime.now(timezone.utc)
    transactions: list[Transaction] = []

    # 1. Normal retail transactions (30)
    normal_senders = [f"ACCT-RETAIL-{i:03d}" for i in range(1, 15)]
    normal_merchants = [f"MERCH-STORE-{i:03d}" for i in range(1, 10)]

    for i in range(30):
        t_time = now - timedelta(hours=random.randint(1, 48), minutes=random.randint(0, 59))
        amt = round(random.uniform(15.0, 240.0), 2)
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=random.choice(normal_senders),
            receiver_account_id=random.choice(normal_merchants),
            amount=Decimal(str(amt)),
            currency="USD",
            transaction_type=TransactionType.PAYMENT,
            status=TransactionStatus.COMPLETED,
            occurred_at=t_time,
            device_id=f"DEV-{random.randint(100, 200)}",
            ip_address=f"192.168.1.{random.randint(10, 80)}",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    # 2. Structuring rapid burst transfers (under $10k threshold, rapid velocity)
    struct_sender = "ACCT-STRUCT-999"
    base_struct_time = now - timedelta(hours=3)
    for i, amt in enumerate([9450.00, 9800.00, 9950.00, 9200.00, 9750.00]):
        # Occurring 1 minute apart -> triggers velocity_1m, velocity_1h, structuring, new receiver
        t_time = base_struct_time + timedelta(minutes=i)
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=struct_sender,
            receiver_account_id=f"ACCT-OFFSHORE-{i+1}",
            amount=Decimal(str(amt)),
            currency="USD",
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.COMPLETED,
            occurred_at=t_time,
            device_id="DEV-STRUCT-01",
            ip_address="104.28.19.44",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    # 3. High-Value Impossible Travel Account Takeover (ATO)
    ato_sender = "ACCT-EXEC-777"
    tx_normal = Transaction(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        sender_account_id=ato_sender,
        receiver_account_id="MERCH-COFFEE-01",
        amount=Decimal("12.50"),
        currency="USD",
        transaction_type=TransactionType.PAYMENT,
        status=TransactionStatus.COMPLETED,
        occurred_at=now - timedelta(hours=2),
        device_id="DEV-IPHONE-15",
        ip_address="12.34.56.78",
        country_code="US",
    )
    db.add(tx_normal)
    transactions.append(tx_normal)

    # 15 minutes later from RU for $28,000
    tx_ato = Transaction(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        sender_account_id=ato_sender,
        receiver_account_id="OFFSHORE-SWISS-01",
        amount=Decimal("28000.00"),
        currency="USD",
        transaction_type=TransactionType.TRANSFER,
        status=TransactionStatus.COMPLETED,
        occurred_at=now - timedelta(hours=1, minutes=45),
        device_id="DEV-TOR-BROWSER",
        ip_address="185.220.101.5",
        country_code="RU",
    )
    db.add(tx_ato)
    transactions.append(tx_ato)

    # 4. Mule Cycle Ring 1 (Length 3: MULE-ALPHA -> MULE-BETA -> MULE-GAMMA -> MULE-ALPHA)
    # Rapid pass-through layering within 20 mins of each other
    base_mule_time = now - timedelta(hours=5)
    mule_cycle_1 = [
        ("MULE-ALPHA", "MULE-BETA", 4500.00, 0),
        ("MULE-BETA", "MULE-GAMMA", 4400.00, 15),
        ("MULE-GAMMA", "MULE-ALPHA", 4300.00, 30),
    ]
    for s, r, amt, mins in mule_cycle_1:
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=s,
            receiver_account_id=r,
            amount=Decimal(str(amt)),
            currency="USD",
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.COMPLETED,
            occurred_at=base_mule_time + timedelta(minutes=mins),
            device_id="DEV-MULE-VPN",
            ip_address="45.154.255.12",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    # 4. Mule Cycle Ring 2 (Length 4: RING-A -> RING-B -> RING-C -> RING-D -> RING-A)
    mule_cycle_2 = [
        ("RING-A", "RING-B", 2500.00, 8),
        ("RING-B", "RING-C", 2450.00, 7),
        ("RING-C", "RING-D", 2400.00, 6),
        ("RING-D", "RING-A", 2350.00, 5),
    ]
    for s, r, amt, h_ago in mule_cycle_2:
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=s,
            receiver_account_id=r,
            amount=Decimal(str(amt)),
            currency="USD",
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.COMPLETED,
            occurred_at=now - timedelta(hours=h_ago),
            device_id="DEV-MULE-2",
            ip_address="185.220.101.5",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    # 5. Fan-In Aggregator Hub (8 senders -> HUB-AGGREGATOR-01)
    hub_agg = "HUB-AGGREGATOR-01"
    for i in range(1, 9):
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=f"FEEDER-ACCT-{i:02d}",
            receiver_account_id=hub_agg,
            amount=Decimal(str(round(random.uniform(1200.0, 3500.0), 2))),
            currency="USD",
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.COMPLETED,
            occurred_at=now - timedelta(hours=random.randint(2, 10)),
            device_id=f"DEV-FEEDER-{i}",
            ip_address=f"10.0.0.{i+10}",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    # 6. Fan-Out Distributor Hub (HUB-DISTRIBUTOR-01 -> 8 receivers)
    hub_dist = "HUB-DISTRIBUTOR-01"
    for i in range(1, 9):
        tx = Transaction(
            id=uuid.uuid4(),
            idempotency_key=str(uuid.uuid4()),
            sender_account_id=hub_dist,
            receiver_account_id=f"DROPOFF-ACCT-{i:02d}",
            amount=Decimal(str(round(random.uniform(400.0, 950.0), 2))),
            currency="USD",
            transaction_type=TransactionType.TRANSFER,
            status=TransactionStatus.COMPLETED,
            occurred_at=now - timedelta(hours=random.randint(1, 8)),
            device_id="DEV-DIST-01",
            ip_address="194.26.29.11",
            country_code="US",
        )
        db.add(tx)
        transactions.append(tx)

    db.commit()
    return transactions


def score_and_alert(db, transactions: list[Transaction], users_map: dict[str, User]):
    # 1. First run network recalculation so topology features exist
    network_res = network_service.recalculate_network_risk(db, lookback_days=30)
    print(f"Network graph calculated: {network_res.accounts_analyzed} accounts, {network_res.mule_suspects_detected} mule suspects, {network_res.cycles_detected} cycles.")

    # 2. Score transactions
    engine = RulesEngine()
    scored_count = 0
    alerts_created: list[Alert] = []

    for tx in transactions:
        features = extract_point_in_time_features(db, tx)
        score, level, reasons, engine_ver = engine.evaluate(tx, features)

        tx.risk_score = score
        tx.risk_level = level
        db.add(tx)

        # Record FraudScore
        fraud_score_record = FraudScore(
            transaction_id=tx.id,
            score=score,
            level=level,
            reasons_json=reasons,
            features_json=features,
            engine_version=engine_ver,
            scored_at=datetime.now(timezone.utc),
        )
        db.add(fraud_score_record)

        # Trigger Alert
        triggered_codes = [r["reason_code"] for r in reasons if r.get("triggered")]
        alert = alert_service.create_alert_if_eligible(
            db=db,
            transaction_id=tx.id,
            risk_score=score,
            risk_level=level,
            reason_codes=triggered_codes,
        )
        if alert:
            alerts_created.append(alert)
        scored_count += 1

    db.commit()
    print(f"Scored {scored_count} transactions. Generated {len(alerts_created)} fraud alerts.")

    # 3. Create Sample Investigations for demonstration
    all_alerts = list(db.execute(select(Alert)).scalars().all())
    existing_inv = db.execute(select(Investigation)).first()

    if all_alerts and not existing_inv:
        investigator = users_map["investigator"]

        # Case 1: Mule Ring Investigation
        mule_alert = next((a for a in all_alerts if any("MULE" in c for c in (a.reason_codes or []))), all_alerts[0])
        inv1 = Investigation(
            title=f"Mule Laundering Ring Alert on Tx {str(mule_alert.transaction_id)[:8]}",
            description="Directed cyclical funds loop identified across 3 accounts (MULE-ALPHA -> MULE-BETA -> MULE-GAMMA). Potential layering operation.",
            priority=InvestigationPriority.HIGH,
            status=InvestigationStatus.IN_PROGRESS,
            owner_id=investigator.id,
        )
        db.add(inv1)
        db.flush()
        inv1.alerts.append(mule_alert)
        mule_alert.status = AlertStatus.INVESTIGATING

        # Add notes and evidence to Case 1
        note1 = InvestigationNote(
            investigation_id=inv1.id,
            author_id=investigator.id,
            body="Investigator verified accounts created within same 48h period using shared VPN IP. Commencing account freeze.",
        )
        db.add(note1)

        evidence1 = InvestigationEvidence(
            investigation_id=inv1.id,
            added_by=investigator.id,
            evidence_type=EvidenceType.ACCOUNT,
            reference_id="MULE-ALPHA",
            description="Transaction flow circular graph: MULE-ALPHA -> MULE-BETA -> MULE-GAMMA -> MULE-ALPHA ($12,900 cumulative).",
        )
        db.add(evidence1)

        # Case 2: Structuring Pattern Case
        if len(all_alerts) > 1:
            struct_alert = all_alerts[1]
            inv2 = Investigation(
                title=f"High Volume Velocity Anomaly on Tx {str(struct_alert.transaction_id)[:8]}",
                description="Repeated sub-$10,000 cash equivalent transfers executed in rapid succession to offshore destination accounts.",
                priority=InvestigationPriority.HIGH,
                status=InvestigationStatus.OPEN,
                owner_id=investigator.id,
            )
            db.add(inv2)
            db.flush()
            inv2.alerts.append(struct_alert)

            note2 = InvestigationNote(
                investigation_id=inv2.id,
                author_id=investigator.id,
                body="Transaction amounts ($9,450, $9,800, $9,950) strongly indicate intentional threshold structuring (CTR evasion).",
            )
            db.add(note2)

        db.commit()
        print("Sample investigation cases created with notes and evidence.")


def main():
    print("=== SentinelX Demo Data Seeding ===")
    db = SessionLocal()
    try:
        users = seed_users(db)
        print("Users seeded (admin, investigator, analyst).")
        transactions = seed_transactions(db)
        print(f"Seeded {len(transactions)} transactions.")
        score_and_alert(db, transactions, users)
        print("Demo seeding completed successfully!")
    finally:
        db.close()


if __name__ == "__main__":
    main()
