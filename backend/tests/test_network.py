from datetime import datetime, timezone
from decimal import Decimal
import uuid
from sqlalchemy import select

from app.core.security import create_access_token
from app.fraud.features import extract_point_in_time_features
from app.fraud.scorer import RulesEngine
from app.models.network import AccountNetworkRisk, NetworkRiskFlag
from app.models.transaction import Transaction, TransactionStatus, TransactionType
from app.models.user import UserRole
from app.services import network_service
from app.tasks.maintenance_tasks import recalculate_network_graph_task


def create_tx(
    db,
    sender: str,
    receiver: str,
    amount: float = 100.0,
    status: TransactionStatus = TransactionStatus.COMPLETED,
    occurred_at: datetime | None = None,
) -> Transaction:
    tx = Transaction(
        id=uuid.uuid4(),
        idempotency_key=str(uuid.uuid4()),
        sender_account_id=sender,
        receiver_account_id=receiver,
        amount=Decimal(str(amount)),
        currency="USD",
        transaction_type=TransactionType.TRANSFER,
        status=status,
        occurred_at=occurred_at or datetime.now(timezone.utc),
    )
    db.add(tx)
    db.commit()
    db.refresh(tx)
    return tx


def test_graph_construction(db_session):
    """Verify directed graph construction from transaction records."""
    create_tx(db_session, "ACC-A", "ACC-B", 250.0)
    create_tx(db_session, "ACC-A", "ACC-B", 150.0)
    create_tx(db_session, "ACC-B", "ACC-C", 300.0)

    G, meta = network_service.build_transaction_graph(db_session, lookback_days=30)
    assert len(G.nodes) == 3
    assert len(G.edges) == 2
    assert G.has_edge("ACC-A", "ACC-B")
    assert G.has_edge("ACC-B", "ACC-C")
    assert G["ACC-A"]["ACC-B"]["weight"] == 400.0
    assert G["ACC-A"]["ACC-B"]["count"] == 2
    assert G["ACC-B"]["ACC-C"]["weight"] == 300.0


def test_detect_mule_cycles(db_session):
    """Verify detection of directed money laundering mule cycles (length 3)."""
    # ACC-1 -> ACC-2 -> ACC-3 -> ACC-1
    create_tx(db_session, "MULE-1", "MULE-2", 500.0)
    create_tx(db_session, "MULE-2", "MULE-3", 480.0)
    create_tx(db_session, "MULE-3", "MULE-1", 450.0)

    G, _ = network_service.build_transaction_graph(db_session)
    cycle_lens, cycle_strs, total_cycles = network_service.detect_mule_cycles(G, min_len=3, max_len=6)

    assert total_cycles >= 1
    assert "MULE-1" in cycle_lens
    assert "MULE-2" in cycle_lens
    assert "MULE-3" in cycle_lens
    assert cycle_lens["MULE-1"] == 3


def test_detect_fan_in_and_fan_out_hubs(db_session):
    """Verify identification of fan-in aggregators and fan-out distributors."""
    # Fan-in aggregator: HUB-IN receives from 4 distinct accounts
    for i in range(1, 5):
        create_tx(db_session, f"SENDER-{i}", "HUB-IN", 100.0)

    # Fan-out distributor: HUB-OUT sends to 4 distinct accounts
    for i in range(1, 5):
        create_tx(db_session, "HUB-OUT", f"RECEIVER-{i}", 100.0)

    res = network_service.recalculate_network_risk(db_session, lookback_days=30)
    assert res.accounts_analyzed >= 10
    assert res.hubs_detected >= 2

    hub_in = network_service.get_account_network_risk(db_session, "HUB-IN")
    assert hub_in is not None
    assert hub_in.risk_flag == NetworkRiskFlag.FAN_IN_AGGREGATOR
    assert hub_in.in_degree == 4

    hub_out = network_service.get_account_network_risk(db_session, "HUB-OUT")
    assert hub_out is not None
    assert hub_out.risk_flag == NetworkRiskFlag.FAN_OUT_DISTRIBUTOR
    assert hub_out.out_degree == 4


def test_recalculate_network_risk_persistence(db_session):
    """Verify recalculate_network_risk persists and updates records in PostgreSQL."""
    create_tx(db_session, "RING-A", "RING-B", 1000.0)
    create_tx(db_session, "RING-B", "RING-C", 950.0)
    create_tx(db_session, "RING-C", "RING-A", 900.0)

    summary = network_service.recalculate_network_risk(db_session, lookback_days=30)
    assert summary.accounts_analyzed == 3
    assert summary.mule_suspects_detected == 3
    assert summary.cycles_detected >= 1

    records = db_session.execute(select(AccountNetworkRisk)).scalars().all()
    assert len(records) == 3
    for r in records:
        assert r.is_mule_suspect is True
        assert r.risk_flag == NetworkRiskFlag.MULE_CYCLE
        assert r.cycle_length == 3


def test_rule_11_mule_ring_triggers_in_scoring(db_session):
    """Verify Rule 11 fires with NETWORK_MULE_RING when account is flagged as mule suspect."""
    # Flag account in account_network_risk
    mule_record = AccountNetworkRisk(
        account_id="SUSPECT-ACC-99",
        centrality_score=0.12,
        in_degree=3,
        out_degree=3,
        fan_in_out_ratio=1.0,
        total_volume_in=5000.0,
        total_volume_out=4800.0,
        is_mule_suspect=True,
        cycle_length=3,
        risk_flag=NetworkRiskFlag.MULE_CYCLE,
        risk_reasons="Mule cycle detected (len=3)",
        calculated_at=datetime.now(timezone.utc),
    )
    db_session.add(mule_record)
    db_session.commit()

    test_tx = create_tx(db_session, "SUSPECT-ACC-99", "NORMAL-RECV", 200.0)
    features = extract_point_in_time_features(db_session, test_tx)

    assert features["network_mule_detected"] is True
    assert features["sender_network_flag"] == "mule_cycle"

    engine = RulesEngine()
    score, risk_level, reasons, _ = engine.evaluate(test_tx, features)

    rule_11_reasons = [r for r in reasons if r["reason_code"] == "NETWORK_MULE_RING"]
    assert len(rule_11_reasons) == 1
    assert rule_11_reasons[0]["triggered"] is True
    assert rule_11_reasons[0]["score_contribution"] == 35


def test_rule_11_clean_account_no_trigger(db_session):
    """Verify clean account does not trigger Rule 11."""
    test_tx = create_tx(db_session, "CLEAN-SENDER", "CLEAN-RECEIVER", 150.0)
    features = extract_point_in_time_features(db_session, test_tx)

    assert features["network_mule_detected"] is False
    assert features["network_hub_detected"] is False

    engine = RulesEngine()
    score, risk_level, reasons, _ = engine.evaluate(test_tx, features)

    rule_11_reasons = [r for r in reasons if r["rule_name"] == "network_risk"]
    assert len(rule_11_reasons) == 0


def test_network_api_endpoints_and_rbac(client, create_test_user, db_session):
    """Verify /api/v1/network endpoints with authentication and RBAC."""
    analyst = create_test_user("analyst_net@test.local", "AnalystPass123!", UserRole.ANALYST)
    admin = create_test_user("admin_net@test.local", "AdminPass123!", UserRole.ADMIN)

    analyst_token = create_access_token(user_id=str(analyst.id), email=analyst.email, role=analyst.role.value)
    admin_token = create_access_token(user_id=str(admin.id), email=admin.email, role=admin.role.value)

    # Seed data
    create_tx(db_session, "NODE-1", "NODE-2", 300.0)
    create_tx(db_session, "NODE-2", "NODE-3", 300.0)
    create_tx(db_session, "NODE-3", "NODE-1", 300.0)
    network_service.recalculate_network_risk(db_session)

    # 1. GET /api/v1/network/accounts/{account_id}
    res = client.get(
        "/api/v1/network/accounts/NODE-1",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["account_id"] == "NODE-1"
    assert data["is_mule_suspect"] is True

    # 404 for nonexistent account
    res_404 = client.get(
        "/api/v1/network/accounts/NONEXISTENT",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_404.status_code == 404

    # 2. GET /api/v1/network/suspicious
    res_susp = client.get(
        "/api/v1/network/suspicious?mule_only=true",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_susp.status_code == 200
    susp_data = res_susp.json()
    assert susp_data["total"] >= 3
    assert len(susp_data["items"]) >= 3

    # 3. GET /api/v1/network/graph
    res_graph = client.get(
        "/api/v1/network/graph",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_graph.status_code == 200
    graph_data = res_graph.json()
    assert graph_data["total_nodes"] >= 3
    assert graph_data["total_edges"] >= 3
    assert graph_data["mule_cycles_count"] >= 3

    # 4. POST /api/v1/network/recalculate RBAC
    # Analyst should be forbidden (HTTP 403)
    res_forbid = client.post(
        "/api/v1/network/recalculate",
        headers={"Authorization": f"Bearer {analyst_token}"},
    )
    assert res_forbid.status_code == 403

    # Admin should succeed (HTTP 200)
    res_recalc = client.post(
        "/api/v1/network/recalculate",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert res_recalc.status_code == 200
    recalc_data = res_recalc.json()
    assert recalc_data["accounts_analyzed"] >= 3


def test_celery_recalculate_network_graph_task(db_session):
    """Verify Celery periodic task runs network recalculation in eager mode."""
    create_tx(db_session, "CEL-1", "CEL-2", 400.0)
    create_tx(db_session, "CEL-2", "CEL-3", 400.0)
    create_tx(db_session, "CEL-3", "CEL-1", 400.0)

    result = recalculate_network_graph_task.apply(kwargs={"lookback_days": 30}).get()
    assert result["status"] == "completed"
    assert result["accounts_analyzed"] >= 3
    assert result["mule_suspects_detected"] >= 3
