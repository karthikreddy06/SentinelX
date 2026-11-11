from fastapi import APIRouter

from app.api.v1.endpoints import audit_logs, auth, transactions, users

api_router = APIRouter()
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(audit_logs.router, prefix="/audit-logs", tags=["audit-logs"])
api_router.include_router(transactions.router, prefix="/transactions", tags=["transactions"])
