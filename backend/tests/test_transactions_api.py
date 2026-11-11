from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest


from app.core.config import settings


@pytest.fixture(autouse=True)
def authenticate_client(client, investigator_headers, monkeypatch):
    monkeypatch.setattr(settings, "FRAUD_SCORING_ENABLED", False)
    client.headers.update(investigator_headers)


def make_valid_payload(**overrides):
    now = datetime.now(timezone.utc)
    base = {
        "sender_account_id": "acc_sender_100",
        "receiver_account_id": "acc_receiver_200",
        "amount": "1234.56",
        "currency": "USD",
        "transaction_type": "transfer",
        "occurred_at": now.isoformat(),
        "description": "Payment for services",
        "channel": "mobile",
        "country_code": "US",
        "device_id": "device_abc_123",
        "ip_address": "192.168.1.1",
    }
    base.update(overrides)
    return base


# === CREATE & STORED VALUES ===

def test_create_transaction_success(client):
    payload = make_valid_payload(amount="9876.54")
    key = str(uuid.uuid4())
    response = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert response.status_code == 201
    data = response.json()

    assert data["idempotency_key"] == key
    assert data["sender_account_id"] == payload["sender_account_id"]
    assert data["receiver_account_id"] == payload["receiver_account_id"]
    assert Decimal(str(data["amount"])) == Decimal("9876.54")
    assert data["currency"] == "USD"
    assert data["transaction_type"] == "transfer"
    assert data["status"] == "pending"
    assert data["risk_score"] is None
    assert data["risk_level"] is None
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


# === IDEMPOTENCY ===

def test_idempotent_replay_success(client):
    payload = make_valid_payload()
    key = "idem-key-replay-1"

    # First request -> 201
    r1 = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r1.status_code == 201
    created_id = r1.json()["id"]

    # Replay request -> 200 with identical ID and data
    r2 = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": key},
    )
    assert r2.status_code == 200
    assert r2.json()["id"] == created_id


def test_idempotency_conflict_different_payload(client):
    payload1 = make_valid_payload(amount="100.00")
    payload2 = make_valid_payload(amount="200.00")
    key = "idem-key-conflict-1"

    r1 = client.post(
        "/api/v1/transactions",
        json=payload1,
        headers={"Idempotency-Key": key},
    )
    assert r1.status_code == 201

    r2 = client.post(
        "/api/v1/transactions",
        json=payload2,
        headers={"Idempotency-Key": key},
    )
    assert r2.status_code == 409
    assert "already used with a different" in r2.json()["detail"]


def test_missing_idempotency_key_rejected(client):
    payload = make_valid_payload()
    response = client.post("/api/v1/transactions", json=payload)
    assert response.status_code == 422


def test_idempotency_key_too_long_rejected(client):
    payload = make_valid_payload()
    response = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": "x" * 101},
    )
    assert response.status_code == 422


# === VALIDATION FAILURES ===

@pytest.mark.parametrize("invalid_amount", ["-10.00", "0.00", "0", "-0.01"])
def test_validation_negative_or_zero_amount(client, invalid_amount):
    payload = make_valid_payload(amount=invalid_amount)
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_validation_three_decimal_places(client):
    payload = make_valid_payload(amount="10.123")
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("bad_currency", ["us", "US", "USDT", "123", "us-", "usd"])
def test_validation_bad_currency(client, bad_currency):
    payload = make_valid_payload(currency=bad_currency)
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_validation_same_sender_receiver(client):
    payload = make_valid_payload(
        sender_account_id="ACC_SAME",
        receiver_account_id="ACC_SAME",
    )
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_validation_future_occurred_at(client):
    future_time = datetime.now(timezone.utc) + timedelta(minutes=10)
    payload = make_valid_payload(occurred_at=future_time.isoformat())
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


def test_validation_naive_datetime(client):
    naive_str = "2026-11-11T12:00:00"  # No timezone offset
    payload = make_valid_payload(occurred_at=naive_str)
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


@pytest.mark.parametrize("extra_field,value", [
    ("risk_score", 50),
    ("risk_level", "high"),
    ("status", "completed"),
    ("id", str(uuid.uuid4())),
])
def test_client_cannot_set_protected_fields(client, extra_field, value):
    payload = make_valid_payload(**{extra_field: value})
    r = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    assert r.status_code == 422


# === GET BY ID ===

def test_get_transaction_by_id(client):
    payload = make_valid_payload()
    created = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()

    # Found
    res = client.get(f"/api/v1/transactions/{created['id']}")
    assert res.status_code == 200
    assert res.json()["id"] == created["id"]

    # 404
    missing_id = str(uuid.uuid4())
    res_404 = client.get(f"/api/v1/transactions/{missing_id}")
    assert res_404.status_code == 404

    # 422 for invalid UUID
    res_422 = client.get("/api/v1/transactions/not-a-valid-uuid")
    assert res_422.status_code == 422


# === LIST PAGINATION AND FILTERS ===

def test_list_pagination_and_filters(client):
    base_time = datetime(2026, 11, 1, 10, 0, tzinfo=timezone.utc)
    tx_ids = []

    # Insert 5 distinct transactions
    for i in range(5):
        payload = make_valid_payload(
            sender_account_id=f"sender_{i % 2}",
            receiver_account_id=f"receiver_{i}",
            amount=f"{(i + 1) * 100}.00",
            occurred_at=(base_time + timedelta(hours=i)).isoformat(),
        )
        res = client.post(
            "/api/v1/transactions",
            json=payload,
            headers={"Idempotency-Key": f"key-list-{i}"},
        )
        assert res.status_code == 201
        tx_ids.append(res.json()["id"])

    # 1. Total and default pagination
    r = client.get("/api/v1/transactions?limit=2&offset=0")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["offset"] == 0

    # Stable sorting: occurred_at DESC (last inserted is first)
    assert body["items"][0]["id"] == tx_ids[4]
    assert body["items"][1]["id"] == tx_ids[3]

    # Offset pagination
    r_page2 = client.get("/api/v1/transactions?limit=2&offset=2")
    assert len(r_page2.json()["items"]) == 2
    assert r_page2.json()["items"][0]["id"] == tx_ids[2]

    # Filter by sender_account_id
    r_sender = client.get("/api/v1/transactions?sender_account_id=sender_0")
    assert r_sender.status_code == 200
    # sender_0 was used for i=0, 2, 4 -> 3 items
    assert r_sender.json()["total"] == 3

    # Filter by min_amount and max_amount
    r_amt = client.get("/api/v1/transactions?min_amount=200.00&max_amount=400.00")
    assert r_amt.json()["total"] == 3  # 200, 300, 400

    # Filter by date range
    dt_from = (base_time + timedelta(hours=1)).isoformat()
    dt_to = (base_time + timedelta(hours=3)).isoformat()
    r_date = client.get(
        "/api/v1/transactions",
        params={"date_from": dt_from, "date_to": dt_to},
    )
    assert r_date.status_code == 200
    assert r_date.json()["total"] == 3

    # Combined filters
    r_comb = client.get(
        "/api/v1/transactions",
        params={"sender_account_id": "sender_0", "min_amount": "200.00"},
    )
    assert r_comb.status_code == 200
    # sender_0 with amount >= 200: i=2 ($300) and i=4 ($500)
    assert r_comb.json()["total"] == 2


# === PATCH (UPDATE) ===

def test_patch_update_allowed_and_forbidden_fields(client):
    payload = make_valid_payload()
    created = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    tx_id = created["id"]

    # Allowed: update description only
    r_desc = client.patch(
        f"/api/v1/transactions/{tx_id}",
        json={"description": "Updated note"},
    )
    assert r_desc.status_code == 200
    assert r_desc.json()["description"] == "Updated note"
    assert r_desc.json()["status"] == "pending"

    # Allowed: update status from pending to completed
    r_stat = client.patch(
        f"/api/v1/transactions/{tx_id}",
        json={"status": "completed"},
    )
    assert r_stat.status_code == 200
    assert r_stat.json()["status"] == "completed"

    # Invalid status transition: completed -> cancelled (409)
    r_inv = client.patch(
        f"/api/v1/transactions/{tx_id}",
        json={"status": "cancelled"},
    )
    assert r_inv.status_code == 409

    # Forbidden field: cannot update amount (422)
    r_forbid = client.patch(
        f"/api/v1/transactions/{tx_id}",
        json={"amount": "999.99"},
    )
    assert r_forbid.status_code == 422


# === CANCEL ===

def test_cancel_transaction_workflow(client):
    # 1. Cancel pending transaction -> succeeds
    payload = make_valid_payload()
    created = client.post(
        "/api/v1/transactions",
        json=payload,
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    tx_id = created["id"]

    r_cancel = client.post(f"/api/v1/transactions/{tx_id}/cancel")
    assert r_cancel.status_code == 200
    assert r_cancel.json()["status"] == "cancelled"

    # Cancelling again -> 409
    r_cancel_again = client.post(f"/api/v1/transactions/{tx_id}/cancel")
    assert r_cancel_again.status_code == 409

    # 2. Cancel completed transaction -> 409
    tx2 = client.post(
        "/api/v1/transactions",
        json=make_valid_payload(),
        headers={"Idempotency-Key": str(uuid.uuid4())},
    ).json()
    client.patch(
        f"/api/v1/transactions/{tx2['id']}",
        json={"status": "completed"},
    )

    r_cancel_completed = client.post(f"/api/v1/transactions/{tx2['id']}/cancel")
    assert r_cancel_completed.status_code == 409
