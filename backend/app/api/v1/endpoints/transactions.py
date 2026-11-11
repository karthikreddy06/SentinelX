from datetime import datetime
from decimal import Decimal
import uuid

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, require_roles
from app.core.config import settings
from app.database.connection import get_db
from app.models.transaction import (
    RiskLevel,
    TransactionStatus,
)
from app.models.user import User, UserRole
from app.schemas.fraud import FraudScoreResponse, RiskHistoryResponse
from app.schemas.transaction import (
    TransactionCreate,
    TransactionListResponse,
    TransactionResponse,
    TransactionUpdate,
)
from app.services.audit_service import record_audit_entry
from app.services.fraud_service import (
    get_transaction_risk_history,
    score_transaction,
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

# RBAC Definitions
CAN_READ_TRANSACTIONS = require_roles(
    UserRole.ADMIN, UserRole.INVESTIGATOR, UserRole.ANALYST
)
CAN_WRITE_TRANSACTIONS = require_roles(UserRole.ADMIN, UserRole.INVESTIGATOR)


@router.post(
    "",
    response_model=TransactionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create transaction",
    description="Create a new transaction with idempotency protection. Restricted to Admin and Investigator roles.",
    responses={
        200: {"description": "Idempotent replay with identical payload returned existing transaction."},
        201: {"description": "Transaction successfully created with status pending."},
        400: {"description": "Missing or invalid headers."},
        401: {"description": "Unauthorized."},
        403: {"description": "Forbidden (Analyst role cannot create transactions)."},
        409: {"description": "Idempotency key already used with different payload."},
        422: {"description": "Validation error in request payload or headers."},
    },
)
def create_transaction_endpoint(
    payload: TransactionCreate,
    request: Request,
    response: Response,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=100,
        description="Unique client idempotency key (1-100 characters).",
    ),
    current_user: User = Depends(CAN_WRITE_TRANSACTIONS),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        transaction, is_created = create_transaction(
            db=db, data=payload, idempotency_key=idempotency_key
        )
        if not is_created:
            response.status_code = status.HTTP_200_OK
        elif settings.FRAUD_SCORING_ENABLED:
            score_transaction(
                db=db,
                transaction_id=transaction.id,
                user=current_user,
                ip_address=ip,
                user_agent=user_agent,
            )
            db.refresh(transaction)

        record_audit_entry(
            db=db,
            action="transaction.create",
            outcome="success",
            user=current_user,
            resource_type="transaction",
            resource_id=str(transaction.id),
            ip_address=ip,
            user_agent=user_agent,
            metadata={
                "amount": str(transaction.amount),
                "currency": transaction.currency,
                "is_replay": not is_created,
            },
        )
        return TransactionResponse.model_validate(transaction)
    except IdempotencyConflict as exc:
        record_audit_entry(
            db=db,
            action="transaction.create",
            outcome="failure",
            user=current_user,
            resource_type="transaction",
            ip_address=ip,
            user_agent=user_agent,
            metadata={"idempotency_key": idempotency_key, "error": "conflict"},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get(
    "",
    response_model=TransactionListResponse,
    summary="List transactions",
    description="Query transactions with pagination and optional filters. Accessible to Admin, Investigator, Analyst.",
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
    current_user: User = Depends(CAN_READ_TRANSACTIONS),
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
    description="Fetch a single transaction by its UUID. Accessible to Admin, Investigator, Analyst.",
    responses={
        404: {"description": "Transaction not found."},
        422: {"description": "Invalid UUID format."},
    },
)
def get_transaction_endpoint(
    id: uuid.UUID,
    current_user: User = Depends(CAN_READ_TRANSACTIONS),
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
    description="Update mutable transaction fields (status and description). Restricted to Admin and Investigator.",
    responses={
        404: {"description": "Transaction not found."},
        409: {"description": "Invalid status transition."},
        422: {"description": "Validation error or forbidden fields."},
    },
)
def update_transaction_endpoint(
    id: uuid.UUID,
    payload: TransactionUpdate,
    request: Request,
    current_user: User = Depends(CAN_WRITE_TRANSACTIONS),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        transaction = update_transaction(db=db, transaction_id=id, data=payload)
        record_audit_entry(
            db=db,
            action="transaction.update",
            outcome="success",
            user=current_user,
            resource_type="transaction",
            resource_id=str(transaction.id),
            ip_address=ip,
            user_agent=user_agent,
            metadata={"new_status": transaction.status.value},
        )
        return TransactionResponse.model_validate(transaction)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStatusTransition as exc:
        record_audit_entry(
            db=db,
            action="transaction.update",
            outcome="failure",
            user=current_user,
            resource_type="transaction",
            resource_id=str(id),
            ip_address=ip,
            user_agent=user_agent,
            metadata={"error": "invalid_status_transition"},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/{id}/cancel",
    response_model=TransactionResponse,
    summary="Cancel transaction",
    description="Cancel a transaction. Only pending transactions can be cancelled. Restricted to Admin and Investigator.",
    responses={
        200: {"description": "Transaction successfully cancelled."},
        404: {"description": "Transaction not found."},
        409: {"description": "Cannot cancel non-pending transaction."},
    },
)
def cancel_transaction_endpoint(
    id: uuid.UUID,
    request: Request,
    current_user: User = Depends(CAN_WRITE_TRANSACTIONS),
    db: Session = Depends(get_db),
) -> TransactionResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        transaction = cancel_transaction(db=db, transaction_id=id)
        record_audit_entry(
            db=db,
            action="transaction.cancel",
            outcome="success",
            user=current_user,
            resource_type="transaction",
            resource_id=str(transaction.id),
            ip_address=ip,
            user_agent=user_agent,
        )
        return TransactionResponse.model_validate(transaction)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidStatusTransition as exc:
        record_audit_entry(
            db=db,
            action="transaction.cancel",
            outcome="failure",
            user=current_user,
            resource_type="transaction",
            resource_id=str(id),
            ip_address=ip,
            user_agent=user_agent,
            metadata={"error": "cannot_cancel_non_pending"},
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.post(
    "/{id}/score",
    response_model=FraudScoreResponse,
    summary="Re-score transaction",
    description="Trigger on-demand fraud scoring using the rules engine. Restricted to Admin and Investigator.",
    responses={
        200: {"description": "Transaction successfully evaluated and scored."},
        401: {"description": "Unauthorized."},
        403: {"description": "Forbidden."},
        404: {"description": "Transaction not found."},
    },
)
def score_transaction_endpoint(
    id: uuid.UUID,
    request: Request,
    current_user: User = Depends(CAN_WRITE_TRANSACTIONS),
    db: Session = Depends(get_db),
) -> FraudScoreResponse:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    try:
        fraud_score = score_transaction(
            db=db,
            transaction_id=id,
            user=current_user,
            ip_address=ip,
            user_agent=user_agent,
        )
        return FraudScoreResponse.model_validate(fraud_score)
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get(
    "/{id}/risk",
    response_model=RiskHistoryResponse,
    summary="Get transaction risk history",
    description="Retrieve full score and reason history for a transaction. Accessible to Admin, Investigator, Analyst.",
    responses={
        200: {"description": "Risk history successfully retrieved."},
        401: {"description": "Unauthorized."},
        404: {"description": "Transaction not found."},
    },
)
def get_transaction_risk_endpoint(
    id: uuid.UUID,
    current_user: User = Depends(CAN_READ_TRANSACTIONS),
    db: Session = Depends(get_db),
) -> RiskHistoryResponse:
    try:
        tx = get_transaction(db=db, transaction_id=id)
        scores = get_transaction_risk_history(db=db, transaction_id=id)
        return RiskHistoryResponse(
            transaction_id=tx.id,
            current_score=tx.risk_score,
            current_level=tx.risk_level,
            scores=[FraudScoreResponse.model_validate(s) for s in scores],
        )
    except TransactionNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
