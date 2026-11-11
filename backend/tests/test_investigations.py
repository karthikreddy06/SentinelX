from datetime import datetime, timedelta, timezone
import uuid

import pytest
from fastapi.testclient import TestClient

from app.models.alert import Alert, AlertEvent, AlertSeverity, AlertStatus
from app.models.investigation import (
    EvidenceType,
    Investigation,
    InvestigationOutcome,
    InvestigationPriority,
    InvestigationStatus,
)
from app.models.user import User, UserRole
from tests.test_alerts import create_high_risk_transaction


def get_or_create_alert(client: TestClient, headers: dict) -> str:
    tx_id = create_high_risk_transaction(client, headers)
    alerts = client.get("/api/v1/alerts", headers=headers).json()["items"]
    alert_id = [a["id"] for a in alerts if a["transaction_id"] == tx_id][0]
    return alert_id


def test_investigation_lifecycle_and_alert_sync(
    client: TestClient, investigator_headers, admin_headers, analyst_headers, db_session
):
    investigator = db_session.query(User).filter(User.role == UserRole.INVESTIGATOR).first()
    assert investigator is not None

    alert_id_1 = get_or_create_alert(client, admin_headers)
    alert_id_2 = get_or_create_alert(client, admin_headers)

    # 1. Create investigation linking alert_id_1
    create_payload = {
        "title": "Suspected Account Takeover Ring",
        "description": "High velocity burst followed by impossible travel in multiple accounts.",
        "priority": "high",
        "alert_ids": [alert_id_1],
    }
    create_resp = client.post(
        "/api/v1/investigations",
        json=create_payload,
        headers=investigator_headers,
    )
    assert create_resp.status_code == 201
    inv_data = create_resp.json()
    inv_id = inv_data["id"]
    assert inv_data["status"] == "open"
    assert inv_data["priority"] == "high"
    assert inv_data["owner_id"] == str(investigator.id)
    assert len(inv_data["alerts"]) == 1

    # Verify linked alert was auto-transitioned to 'investigating'
    alert_1 = client.get(f"/api/v1/alerts/{alert_id_1}", headers=analyst_headers).json()
    assert alert_1["status"] == "investigating"

    # 2. Add a note (should progress investigation status to in_progress)
    note_resp = client.post(
        f"/api/v1/investigations/{inv_id}/notes",
        json={"body": "Reviewed IP address telemetry. Matches known Tor exit node."},
        headers=investigator_headers,
    )
    assert note_resp.status_code == 201
    assert note_resp.json()["body"] == "Reviewed IP address telemetry. Matches known Tor exit node."

    # 3. Attach evidence
    ev_resp = client.post(
        f"/api/v1/investigations/{inv_id}/evidence",
        json={
            "evidence_type": "external_document",
            "reference_id": "TOR-NODE-7819",
            "description": "Public Tor exit node registry record.",
        },
        headers=investigator_headers,
    )
    assert ev_resp.status_code == 201
    assert ev_resp.json()["evidence_type"] == "external_document"

    # 4. Link additional alert (alert_id_2)
    link_resp = client.post(
        f"/api/v1/investigations/{inv_id}/alerts",
        json={"alert_ids": [alert_id_2]},
        headers=investigator_headers,
    )
    assert link_resp.status_code == 200
    assert len(link_resp.json()["alerts"]) == 2

    alert_2 = client.get(f"/api/v1/alerts/{alert_id_2}", headers=analyst_headers).json()
    assert alert_2["status"] == "investigating"

    # 5. Check aggregated timeline on GET
    get_resp = client.get(f"/api/v1/investigations/{inv_id}", headers=analyst_headers)
    assert get_resp.status_code == 200
    timeline = get_resp.json()["timeline"]
    event_types = [t["event_type"] for t in timeline]
    assert "investigation_created" in event_types
    assert "note" in event_types
    assert "evidence" in event_types
    assert "alert_event" in event_types

    # 6. Close investigation with confirmed_fraud
    close_resp = client.post(
        f"/api/v1/investigations/{inv_id}/close",
        json={"outcome": "confirmed_fraud", "closing_notes": "Stolen credentials used from Tor exit node."},
        headers=investigator_headers,
    )
    assert close_resp.status_code == 200
    closed_data = close_resp.json()
    assert closed_data["status"] == "closed"
    assert closed_data["outcome"] == "confirmed_fraud"
    assert closed_data["closed_at"] is not None

    # Verify linked alerts automatically transitioned to resolved_fraud
    alert_1_closed = client.get(f"/api/v1/alerts/{alert_id_1}", headers=analyst_headers).json()
    alert_2_closed = client.get(f"/api/v1/alerts/{alert_id_2}", headers=analyst_headers).json()
    assert alert_1_closed["status"] == "resolved_fraud"
    assert alert_2_closed["status"] == "resolved_fraud"

    # 7. Modifying closed investigation fails
    mod_fail = client.post(
        f"/api/v1/investigations/{inv_id}/notes",
        json={"body": "Late note"},
        headers=investigator_headers,
    )
    assert mod_fail.status_code == 400

    # 8. Reopen investigation (admin only)
    # Investigator cannot reopen
    reopen_inv_fail = client.post(
        f"/api/v1/investigations/{inv_id}/reopen",
        headers=investigator_headers,
    )
    assert reopen_inv_fail.status_code == 403

    # Admin reopens
    reopen_resp = client.post(
        f"/api/v1/investigations/{inv_id}/reopen",
        headers=admin_headers,
    )
    assert reopen_resp.status_code == 200
    reopened_data = reopen_resp.json()
    assert reopened_data["status"] == "in_progress"
    assert reopened_data["outcome"] is None
    assert reopened_data["closed_at"] is None


def test_investigation_rbac_and_filters(
    client: TestClient, analyst_headers, investigator_headers, admin_headers, db_session
):
    # Unauthenticated rejected
    assert client.get("/api/v1/investigations").status_code == 401
    assert client.post("/api/v1/investigations", json={"title": "test"}).status_code == 401

    # Analyst cannot create (403)
    create_analyst = client.post(
        "/api/v1/investigations",
        json={"title": "Analyst Case", "priority": "low"},
        headers=analyst_headers,
    )
    assert create_analyst.status_code == 403

    # Investigator creates investigation
    create_inv = client.post(
        "/api/v1/investigations",
        json={"title": "Investigator Case 1", "priority": "urgent"},
        headers=investigator_headers,
    )
    assert create_inv.status_code == 201
    inv_id = create_inv.json()["id"]

    # Analyst can view list and filters
    list_resp = client.get(f"/api/v1/investigations?priority=urgent", headers=analyst_headers)
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] >= 1

    # Analyst cannot add evidence or notes
    ev_fail = client.post(
        f"/api/v1/investigations/{inv_id}/evidence",
        json={"evidence_type": "account", "reference_id": "acc_1", "description": "desc"},
        headers=analyst_headers,
    )
    assert ev_fail.status_code == 403

    note_fail = client.post(
        f"/api/v1/investigations/{inv_id}/notes",
        json={"body": "note"},
        headers=analyst_headers,
    )
    assert note_fail.status_code == 403
