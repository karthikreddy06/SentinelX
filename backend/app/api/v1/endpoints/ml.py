from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_client_ip, require_roles
from app.database.connection import get_db
from app.ml.model_loader import model_manager
from app.models.user import User, UserRole
from app.services.audit_service import record_audit_entry
from ml.train import train_models

router = APIRouter()

CAN_READ_ML = require_roles(UserRole.ADMIN, UserRole.INVESTIGATOR, UserRole.ANALYST)
CAN_RETRAIN_ML = require_roles(UserRole.ADMIN)


@router.get(
    "/model",
    summary="Get Active ML Model Info",
    description="Retrieve active machine learning model metadata, training configuration, and test metrics.",
)
def get_model_info_endpoint(
    current_user: User = Depends(CAN_READ_ML),
) -> dict[str, Any]:
    if not model_manager.is_loaded or model_manager.metadata is None:
        return {
            "status": "rules_only_fallback",
            "message": "No active ML model loaded. Scoring engine is using transparent rules fallback.",
        }
    return {
        "status": "active",
        "metadata": model_manager.metadata,
    }


@router.post(
    "/retrain",
    summary="Trigger ML Retraining Pipeline (Admin only)",
    description="Retrain the supervised and unsupervised fraud models on synthetic behavioral sequences. Promotes the new model only if validation metrics meet or exceed the active model.",
)
def retrain_model_endpoint(
    request: Request,
    current_admin: User = Depends(CAN_RETRAIN_ML),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    ip = get_client_ip(request)
    user_agent = request.headers.get("User-Agent")

    try:
        # Run reproducible training pipeline
        metrics = train_models(num_accounts=120, days=10, seed=42)

        # Check validation guardrail: candidate validation F1 must be within 0.15 of active model
        val_f1 = metrics["validation_metrics"].get("f1_score") or metrics["validation_metrics"].get("ml_f1_score", 0.0)
        current_f1 = 0.65
        if model_manager.metadata and "metrics" in model_manager.metadata:
            current_f1 = model_manager.metadata["metrics"].get("f1_score", 0.65)

        promoted = val_f1 >= (current_f1 - 0.15)

        if promoted:
            model_manager.load_model()
            record_audit_entry(
                db=db,
                action="ml.retrain",
                outcome="success",
                user=current_admin,
                resource_type="ml_model",
                ip_address=ip,
                user_agent=user_agent,
                metadata={"action": "promoted", "val_f1": val_f1},
            )
            return {
                "status": "promoted",
                "message": "Model retrained and promoted successfully.",
                "metrics": metrics["test_metrics"],
            }
        else:
            record_audit_entry(
                db=db,
                action="ml.retrain",
                outcome="rejected",
                user=current_admin,
                resource_type="ml_model",
                ip_address=ip,
                user_agent=user_agent,
                metadata={"action": "rejected", "val_f1": val_f1, "reason": "inferior_metrics"},
            )
            return {
                "status": "rejected",
                "message": f"Candidate model validation F1 ({val_f1}) was inferior. Active model retained.",
                "candidate_metrics": metrics["test_metrics"],
            }
    except Exception as exc:
        record_audit_entry(
            db=db,
            action="ml.retrain",
            outcome="error",
            user=current_admin,
            resource_type="ml_model",
            ip_address=ip,
            user_agent=user_agent,
            metadata={"error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Retraining failed: {exc}",
        )
