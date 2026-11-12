from datetime import datetime
from decimal import Decimal
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.fraud.cache import invalidate_sender_profile
from app.models.transaction import (
    RiskLevel,
    Transaction,
    TransactionStatus,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionUpdate,
)
from app.services.outbox_service import record_outbox_event


class TransactionServiceError(Exception):
    """Base exception for transaction service errors."""
    pass


class TransactionNotFound(TransactionServiceError):
    """Raised when a requested transaction is not found."""
    pass


class IdempotencyConflict(TransactionServiceError):
    """Raised when an idempotency key is reused with a different payload."""
    pass


class InvalidStatusTransition(TransactionServiceError):
    """Raised when an invalid status transition is requested."""
    pass


TERMINAL_STATUSES = {
    TransactionStatus.COMPLETED,
    TransactionStatus.FAILED,
    TransactionStatus.CANCELLED,
}


def _is_payload_identical(existing: Transaction, data: TransactionCreate) -> bool:
    """Compare fields of existing transaction against incoming create payload."""
    return (
        existing.sender_account_id == data.sender_account_id
        and existing.receiver_account_id == data.receiver_account_id
        and existing.amount == data.amount
        and existing.currency == data.currency
        and existing.transaction_type == data.transaction_type
        and existing.occurred_at == data.occurred_at
        and (existing.description or None) == (data.description or None)
        and (existing.channel or None) == (data.channel or None)
        and (existing.country_code or None) == (data.country_code or None)
        and (existing.device_id or None) == (data.device_id or None)
        and (existing.ip_address or None) == (data.ip_address or None)
    )


def create_transaction(
    db: Session, data: TransactionCreate, idempotency_key: str
) -> tuple[Transaction, bool]:
    """
    Create a new transaction or return an existing one if the idempotency key matches.

    Returns:
        tuple[Transaction, bool]: (transaction, is_created)
        - If new record created: (transaction, True)
        - If idempotency replay with identical payload: (transaction, False)

    Raises:
        IdempotencyConflict: If idempotency key was already used with different payload.
    """
    # 1. Check existing record
    existing = db.execute(
        select(Transaction).where(Transaction.idempotency_key == idempotency_key)
    ).scalar_one_or_none()

    if existing is not None:
        if _is_payload_identical(existing, data):
            return existing, False
        raise IdempotencyConflict(
            f"Idempotency key '{idempotency_key}' was already used with a different transaction payload."
        )

    # 2. Attempt insert
    transaction = Transaction(
        idempotency_key=idempotency_key,
        sender_account_id=data.sender_account_id,
        receiver_account_id=data.receiver_account_id,
        amount=data.amount,
        currency=data.currency,
        transaction_type=data.transaction_type,
        status=TransactionStatus.PENDING,
        occurred_at=data.occurred_at,
        description=data.description,
        channel=data.channel,
        country_code=data.country_code,
        device_id=data.device_id,
        ip_address=data.ip_address,
        risk_score=None,
        risk_level=None,
    )
    db.add(transaction)
    db.flush()

    # Record transactional outbox event atomically
    record_outbox_event(
        db=db,
        event_type="transactions.created",
        topic=settings.KAFKA_TOPIC_TRANSACTIONS_CREATED,
        payload={
            "transaction_id": str(transaction.id),
            "sender_account_id": transaction.sender_account_id,
            "receiver_account_id": transaction.receiver_account_id,
            "amount": float(transaction.amount),
            "currency": transaction.currency,
            "transaction_type": transaction.transaction_type.value,
            "occurred_at": transaction.occurred_at.isoformat(),
            "channel": transaction.channel,
            "country_code": transaction.country_code,
            "device_id": transaction.device_id,
            "ip_address": transaction.ip_address,
        },
    )

    try:
        db.commit()
        db.refresh(transaction)
        invalidate_sender_profile(transaction.sender_account_id)
        return transaction, True
    except IntegrityError:
        db.rollback()
        # Handle concurrent race condition: another thread/process inserted with the same key
        race_existing = db.execute(
            select(Transaction).where(Transaction.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if race_existing is not None:
            if _is_payload_identical(race_existing, data):
                return race_existing, False
            raise IdempotencyConflict(
                f"Idempotency key '{idempotency_key}' was already used with a different transaction payload."
            )
        raise


def get_transaction(db: Session, transaction_id: uuid.UUID) -> Transaction:
    """Fetch transaction by UUID."""
    transaction = db.execute(
        select(Transaction).where(Transaction.id == transaction_id)
    ).scalar_one_or_none()
    if not transaction:
        raise TransactionNotFound(f"Transaction with ID '{transaction_id}' not found.")
    return transaction


def list_transactions(
    db: Session,
    limit: int = 20,
    offset: int = 0,
    status: TransactionStatus | None = None,
    risk_level: RiskLevel | None = None,
    min_amount: Decimal | None = None,
    max_amount: Decimal | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    sender_account_id: str | None = None,
    receiver_account_id: str | None = None,
) -> tuple[list[Transaction], int]:
    """Query transactions with pagination and optional filters."""
    query = select(Transaction)
    count_query = select(func.count(Transaction.id))

    conditions = []
    if status is not None:
        conditions.append(Transaction.status == status)
    if risk_level is not None:
        conditions.append(Transaction.risk_level == risk_level)
    if min_amount is not None:
        conditions.append(Transaction.amount >= min_amount)
    if max_amount is not None:
        conditions.append(Transaction.amount <= max_amount)
    if date_from is not None:
        conditions.append(Transaction.occurred_at >= date_from)
    if date_to is not None:
        conditions.append(Transaction.occurred_at <= date_to)
    if sender_account_id is not None:
        conditions.append(Transaction.sender_account_id == sender_account_id)
    if receiver_account_id is not None:
        conditions.append(Transaction.receiver_account_id == receiver_account_id)

    if conditions:
        query = query.where(*conditions)
        count_query = count_query.where(*conditions)

    total = db.execute(count_query).scalar_one()

    # Stable paging: occurred_at DESC, id DESC
    query = query.order_by(
        Transaction.occurred_at.desc(),
        Transaction.id.desc(),
    ).limit(limit).offset(offset)

    items = list(db.execute(query).scalars().all())
    return items, total


def update_transaction(
    db: Session, transaction_id: uuid.UUID, data: TransactionUpdate
) -> Transaction:
    """
    Update mutable transaction fields (status and description).
    Amount, accounts, currency, type, and occurred_at are immutable.
    Terminal statuses (completed, failed, cancelled) cannot be transitioned.
    """
    transaction = get_transaction(db, transaction_id)
    update_data = data.model_dump(exclude_unset=True)

    if "status" in update_data and update_data["status"] is not None:
        new_status = data.status
        if new_status != transaction.status:
            # Transitions only allowed from pending
            if transaction.status in TERMINAL_STATUSES:
                raise InvalidStatusTransition(
                    f"Cannot transition transaction from terminal status '{transaction.status.value}' to '{new_status.value}'."
                )
            # pending can only transition to completed, failed, or cancelled
            if new_status not in TERMINAL_STATUSES:
                raise InvalidStatusTransition(
                    f"Invalid status transition from '{transaction.status.value}' to '{new_status.value}'."
                )
            transaction.status = new_status

    if "description" in update_data:
        transaction.description = data.description

    db.commit()
    db.refresh(transaction)
    return transaction


def cancel_transaction(db: Session, transaction_id: uuid.UUID) -> Transaction:
    """Cancel a transaction. Only pending transactions can be cancelled."""
    transaction = get_transaction(db, transaction_id)

    if transaction.status != TransactionStatus.PENDING:
        raise InvalidStatusTransition(
            f"Cannot cancel transaction with status '{transaction.status.value}'. Only 'pending' transactions can be cancelled."
        )

    transaction.status = TransactionStatus.CANCELLED
    db.commit()
    db.refresh(transaction)
    return transaction
