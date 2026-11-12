from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.database.connection import get_db
from app.models.network import NetworkRiskFlag
from app.models.user import User, UserRole
from app.schemas.network import (
    AccountNetworkRiskResponse,
    NetworkGraphResponse,
    RecalculateNetworkResponse,
    SuspiciousAccountsListResponse,
)
from app.services import network_service

router = APIRouter()

analyst_or_higher = require_roles(UserRole.ANALYST, UserRole.INVESTIGATOR, UserRole.ADMIN)
investigator_or_higher = require_roles(UserRole.INVESTIGATOR, UserRole.ADMIN)


@router.get(
    "/accounts/{account_id}",
    response_model=AccountNetworkRiskResponse,
    summary="Get network risk profile for a specific account",
)
def get_account_risk(
    account_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> AccountNetworkRiskResponse:
    risk = network_service.get_account_network_risk(db, account_id)
    if not risk:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network risk profile for account '{account_id}' not found",
        )
    return AccountNetworkRiskResponse.model_validate(risk)


@router.get(
    "/suspicious",
    response_model=SuspiciousAccountsListResponse,
    summary="List suspicious accounts identified by network graph analysis",
)
def get_suspicious_accounts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    flag: NetworkRiskFlag | None = Query(None),
    mule_only: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> SuspiciousAccountsListResponse:
    items, total = network_service.list_suspicious_accounts(
        db=db,
        skip=skip,
        limit=limit,
        flag=flag,
        mule_only=mule_only,
    )
    return SuspiciousAccountsListResponse(
        items=[AccountNetworkRiskResponse.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/graph",
    response_model=NetworkGraphResponse,
    summary="Get network graph nodes and edges for visualization",
)
def get_graph(
    min_amount: float = Query(0.0, ge=0.0),
    limit: int = Query(100, ge=1, le=500),
    account_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(analyst_or_higher),
) -> NetworkGraphResponse:
    return network_service.get_network_graph(
        db=db,
        min_amount=min_amount,
        limit=limit,
        account_id=account_id,
    )


@router.post(
    "/recalculate",
    response_model=RecalculateNetworkResponse,
    summary="Recalculate network graph topology, PageRank, and mule cycles",
)
def trigger_network_recalculation(
    lookback_days: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(investigator_or_higher),
) -> RecalculateNetworkResponse:
    return network_service.recalculate_network_risk(db=db, lookback_days=lookback_days)

