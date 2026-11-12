from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient

from app.models.user import User, UserRole


def create_high_risk_transaction(client: TestClient, headers: dict) -> str:
    """Helper that generates a burst of transactions leading to a high-risk transaction that triggers an auto-alert."""
    now = datetime.now(timezone.utc)
    sender = f"burst_acc_{uuid.uuid4().hex[:8]}"

    # Seed 5 prior transactions to trigger velocity burst and baseline history
    for i in range(5):
        resp = client.post(
            "/api/v1/transactions",
            json={
                "amount": "100.00",
                "currency": "USD",
                "sender_account_id": sender,
                "receiver_account_id": f"acc_recv_{i}",
                "transaction_type": "transfer",
                "country_code": "US",
                "occurred_at": (now - timedelta(seconds=10 * (5 - i))).isoformat(),
            },
            headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
        )
        assert resp.status_code == 201

    # Now target transaction: high amount, new receiver, different country (impossible travel)
    target_resp = client.post(
        "/api/v1/transactions",
        json={
            "amount": "8000.00",
            "currency": "USD",
            "sender_account_id": sender,
            "receiver_account_id": f"target_recv_{uuid.uuid4().hex[:6]}",
            "transaction_type": "transfer",
            "country_code": "GB",
            "occurred_at": now.isoformat(),
        },
        headers={**headers, "Idempotency-Key": str(uuid.uuid4())},
    )
    assert target_resp.status_code == 201
    return target_resp.json()["id"]


def test_auto_alert_generation_and_deduplication(client: TestClient, admin_headers, db_session):
    tx_id = create_high_risk_transaction(client, admin_headers)

    # 1. Check if alert was auto-created during creation scoring
    alerts_resp = client.get("/api/v1/alerts", headers=admin_headers)
    assert alerts_resp.status_code == 200
    alerts = alerts_resp.json()["items"]
    matching_alerts = [a for a in alerts if a["transaction_id"] == tx_id]
    assert len(matching_alerts) == 1
    alert = matching_alerts[0]
    assert alert["status"] == "open"
    assert alert["risk_score"] >= 70
    assert len(alert["reason_codes"]) > 0

    # 2. Test deduplication: Re-score transaction and verify alert count remains 1
    rescore_resp = client.post(f"/api/v1/transactions/{tx_id}/score", headers=admin_headers)
    assert rescore_resp.status_code == 200

    alerts_resp2 = client.get("/api/v1/alerts", headers=admin_headers)
    matching_alerts2 = [a for a in alerts_resp2.json()["items"] if a["transaction_id"] == tx_id]
    assert len(matching_alerts2) == 1, "Deduplication failed: created multiple open alerts for same transaction"


def test_alert_state_machine_and_timeline(client: TestClient, investigator_headers, admin_headers, analyst_headers, db_session):
    investigator = db_session.query(User).filter(User.role == UserRole.INVESTIGATOR).first()
    assert investigator is not None

    tx_id = create_high_risk_transaction(client, admin_headers)

    alerts_resp = client.get("/api/v1/alerts?min_score=70", headers=admin_headers)
    matching = [a for a in alerts_resp.json()["items"] if a["transaction_id"] == tx_id]
    assert len(matching) == 1
    alert_id = matching[0]["id"]

    # 1. Assign alert to investigator
    assign_resp = client.post(
        f"/api/v1/alerts/{alert_id}/assign",
        json={"assigned_to": str(investigator.id), "notes": "Assigning for review"},
        headers=investigator_headers,
    )
    assert assign_resp.status_code == 200
    assert assign_resp.json()["assigned_to"] == str(investigator.id)

    # 2. Transition open -> acknowledged
    ack_resp = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "acknowledged", "notes": "Acknowledged by investigator"},
        headers=investigator_headers,
    )
    assert ack_resp.status_code == 200
    assert ack_resp.json()["status"] == "acknowledged"

    # 3. Transition acknowledged -> investigating
    inv_resp = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "investigating", "notes": "Under active investigation"},
        headers=investigator_headers,
    )
    assert inv_resp.status_code == 200
    assert inv_resp.json()["status"] == "investigating"

    # 4. Attempt to resolve without resolution notes (must fail with 400)
    fail_res = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "resolved_fraud", "resolution_notes": ""},
        headers=investigator_headers,
    )
    assert fail_res.status_code == 400

    # 5. Successfully resolve with resolution notes
    res_resp = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "resolved_fraud", "resolution_notes": "Confirmed stolen card credentials"},
        headers=investigator_headers,
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["status"] == "resolved_fraud"
    assert res_resp.json()["resolution_notes"] == "Confirmed stolen card credentials"

    # 6. Attempt to transition from terminal state (must fail with 400)
    term_fail = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "investigating"},
        headers=investigator_headers,
    )
    assert term_fail.status_code == 400

    # 7. Check full timeline of events
    get_resp = client.get(f"/api/v1/alerts/{alert_id}", headers=analyst_headers)
    assert get_resp.status_code == 200
    events = get_resp.json()["events"]
    actions = [e["action"] for e in events]
    assert "created" in actions
    assert "assigned" in actions
    assert "status_changed" in actions


def test_dismiss_and_admin_reopen(client: TestClient, investigator_headers, admin_headers, analyst_headers):
    tx_id = create_high_risk_transaction(client, admin_headers)

    alerts = client.get("/api/v1/alerts", headers=admin_headers).json()["items"]
    alert_id = [a["id"] for a in alerts if a["transaction_id"] == tx_id][0]

    # Dismiss alert as investigator
    dis_resp = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "dismissed", "notes": "False alarm upon quick glance"},
        headers=investigator_headers,
    )
    assert dis_resp.status_code == 200
    assert dis_resp.json()["status"] == "dismissed"

    # Investigator tries to reopen dismissed alert (forbidden: 403)
    reopen_inv = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "open", "notes": "Reopening"},
        headers=investigator_headers,
    )
    assert reopen_inv.status_code == 403

    # Admin reopens dismissed alert (allowed: 200)
    reopen_admin = client.patch(
        f"/api/v1/alerts/{alert_id}",
        json={"status": "open", "notes": "Admin reopening alert for investigation"},
        headers=admin_headers,
    )
    assert reopen_admin.status_code == 200
    assert reopen_admin.json()["status"] == "open"


def test_alert_rbac_and_stats(client: TestClient, analyst_headers, investigator_headers, admin_headers):
    # Unauthenticated rejected
    assert client.get("/api/v1/alerts").status_code == 401
    assert client.get("/api/v1/alerts/stats/summary").status_code == 401

    # Analyst can read stats
    stats_resp = client.get("/api/v1/alerts/stats/summary", headers=analyst_headers)
    assert stats_resp.status_code == 200
    data = stats_resp.json()
    assert "counts_by_status" in data
    assert "counts_by_severity" in data
    assert "open_alerts_count" in data

    # Analyst cannot modify alerts (403)
    dummy_id = str(uuid.uuid4())
    patch_resp = client.patch(
        f"/api/v1/alerts/{dummy_id}",
        json={"status": "acknowledged"},
        headers=analyst_headers,
    )
    assert patch_resp.status_code == 403

    assign_resp = client.post(
        f"/api/v1/alerts/{dummy_id}/assign",
        json={},
        headers=analyst_headers,
    )
    assert assign_resp.status_code == 403
