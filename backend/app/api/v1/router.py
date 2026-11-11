from fastapi import APIRouter

from app.api.v1.endpoints import alerts, audit_logs, auth, ml, transactions, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
api_router.include_router(ml.router, prefix="/ml", tags=["ml"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
