from datetime import datetime
from pydantic import BaseModel, ConfigDict

from app.models.network import NetworkRiskFlag


class AccountNetworkRiskResponse(BaseModel):
    account_id: str
    centrality_score: float
    in_degree: int
    out_degree: int
    fan_in_out_ratio: float
    total_volume_in: float
    total_volume_out: float
    is_mule_suspect: bool
    cycle_length: int | None = None
    community_id: int | None = None
    risk_flag: NetworkRiskFlag
    risk_reasons: str | None = None
    calculated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NetworkGraphNode(BaseModel):
    id: str
    label: str
    centrality: float = 0.0
    in_degree: int = 0
    out_degree: int = 0
    is_mule: bool = False
    risk_flag: str = "normal"
    total_volume: float = 0.0


class NetworkGraphEdge(BaseModel):
    id: str
    source: str
    target: str
    amount: float
    count: int = 1
    timestamp: datetime | None = None


class NetworkGraphResponse(BaseModel):
    nodes: list[NetworkGraphNode]
    edges: list[NetworkGraphEdge]
    total_nodes: int
    total_edges: int
    mule_cycles_count: int


class SuspiciousAccountsListResponse(BaseModel):
    items: list[AccountNetworkRiskResponse]
    total: int


class RecalculateNetworkResponse(BaseModel):
    accounts_analyzed: int
    mule_suspects_detected: int
    cycles_detected: int
    hubs_detected: int
    calculated_at: datetime

