from datetime import datetime
from typing import Any
import uuid

from pydantic import BaseModel, ConfigDict

from app.models.transaction import RiskLevel


class RuleReasonResponse(BaseModel):
    rule_name: str
    reason_code: str
    triggered: bool
    weight: int
    score_contribution: int
    description: str
    evidence: dict[str, Any]


class FraudScoreResponse(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    score: int
    level: RiskLevel
    engine_version: str
    reasons_json: list[dict[str, Any]]
    features_json: dict[str, Any]
    scored_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RiskHistoryResponse(BaseModel):
    transaction_id: uuid.UUID
    current_score: int | None = None
    current_level: RiskLevel | None = None
    scores: list[FraudScoreResponse]
