from datetime import datetime
from decimal import Decimal
import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.database.connection import get_db
from app.models.transaction import (
    RiskLevel,
    TransactionStatus,
)
from app.schemas.transaction import (
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    TransactionUpdate,
)
from app.services.transaction_service import (
    IdempotencyConflict,
    InvalidStatusTransition,
    TransactionNotFound,
    cancel_transaction,
    create_transaction,
    get_transaction,
    list_transactions,
    update_transaction,
)

router = APIRouter()


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create transaction",
    description="Create a new transaction with idempotency protection. If replayed with identical payload, returns 200.",
    responses={
        200: {"description": "Idempotent replay with identical payload returned existing transaction."},
        201: {"description": "Transaction successfully created with status pending."},
        400: {"description": "Missing or invalid headers."},
        409: {"description": "Idempotency key already used with different payload."},
        422: {"description": "Validation error in request payload or headers."},
    },
)
def create_transaction_endpoint(
    payload: TransactionCreate,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=100,
        description="Unique client idempotency key (1-100 characters).",
    ),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    try:
        transaction, is_created = create_transaction(
            db=db, data=payload, idempotency_key=idempotency_key
        )
        if not is_created:
            response.status_code = status.HTTP_200_OK
        return TransactionResponse.model_validate(transaction)
    except IdempotencyConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "",
    response_model=TransactionListResponse,
    summary="List transactions",
    description="Query transactions with pagination and optional filters. Ordered by occurred_at DESC, id DESC.",
)
def list_transactions_endpoint(
    limit: int = Query(default=20, ge=1, le=100, description="Items per page (1-100)."),
    offset: int = Query(default=0, ge=0, description="Offset for pagination."),
    status_filter: TransactionStatus | None = Query(
        default=None, alias="status", description="Filter by transaction status."
    ),
    risk_level: RiskLevel | None = Query(
        default=None, description="Filter by risk level."
    ),
    min_amount: Decimal | None = Query(
        default=None, ge=0, description="Minimum amount."
    ),
    max_amount: Decimal | None = Query(
        default=None, ge=0, description="Maximum amount."
    ),
    date_from: datetime | None = Query(
        default=None, description="Filter occurred_at >= date_from."
    ),
    date_to: datetime | None = Query(
        default=None, description="Filter occurred_at <= date_to."
    ),
    sender_account_id: str | None = Query(
        default=None, max_length=64, description="Filter by sender account."
    ),
    receiver_account_id: str | None = Query(
        default=None, max_length=64, description="Filter by receiver account."
    ),
    db: Session = Depends(get_db),
) -> TransactionListResponse:
    items, total = list_transactions(
        db=db,
        limit=limit,
        offset=offset,
        status=status_filter,
        risk_level=risk_level,
        min_amount=min_amount,
        max_amount=max_amount,
        date_from=date_from,
        date_to=date_to,
        sender_account_id=sender_account_id,
        receiver_account_id=receiver_account_id,
    )
    return TransactionListResponse(
        items=[TransactionResponse.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{id}",
    response_model=TransactionResponse,
    summary="Get transaction by ID",
    description="Fetch a single transaction by its UUID.",
    responses={
        404: {"description": "Transaction not found."},
        422: {"description": "Invalid UUID format."},
    },
)
def get_transaction_endpoint(
    id: uuid.UUID,
    db: Session = Depends(get_db),
) -> TransactionResponse:
    try:
        transaction = get_transaction(db=db, transaction_id=id)
        return TransactionResponse.model_validate(transaction)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.patch(
    "/{id}",
    response_model=TransactionResponse,
    summary="Update transaction",
    description="Update mutable transaction fields (status and description). Terminal statuses cannot transition.",
    responses={
        404: {"description": "Transaction not found."},
        409: {"description": "Invalid status transition."},
        422: {"description": "Validation error or forbidden fields."},
    },
)
def update_transaction_endpoint(
    id: uuid.UUID,
    payload: TransactionUpdate,
    db: Session = Depends(get_db),
) -> TransactionResponse:
    try:
        transaction = update_transaction(db=db, transaction_id=id, data=payload)
        return TransactionResponse.model_validate(transaction)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStatusTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/{id}/cancel",
    response_model=TransactionResponse,
    summary="Cancel transaction",
    description="Cancel a transaction. Only pending transactions can be cancelled.",
    responses={
        200: {"description": "Transaction successfully cancelled."},
        404: {"description": "Transaction not found."},
        409: {"description": "Cannot cancel non-pending transaction."},
    },
)
def cancel_transaction_endpoint(
    id: uuid.UUID,
    db: Session = Depends(get_db),
) -> TransactionResponse:
    try:
        transaction = cancel_transaction(db=db, transaction_id=id)
        return TransactionResponse.model_validate(transaction)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStatusTransition as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
