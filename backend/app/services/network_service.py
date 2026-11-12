from datetime import datetime, timezone, timedelta
from typing import Any
import networkx as nx
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.network import AccountNetworkRisk, NetworkRiskFlag
from app.models.transaction import Transaction, TransactionStatus
from app.schemas.network import (
    NetworkGraphEdge,
    NetworkGraphNode,
    NetworkGraphResponse,
    RecalculateNetworkResponse,
)


def build_transaction_graph(
    db: Session,
    lookback_days: int = 30,
    min_amount: float = 0.0,
    account_id: str | None = None,
) -> tuple[nx.DiGraph, dict[str, dict[str, Any]]]:
    """
    Builds a directed graph G from transactions in the database.
    Nodes are account IDs, directed edges are money flows (sender -> receiver).
    Returns (DiGraph, edge_metadata_dict).
    """
    G = nx.DiGraph()
    edge_metadata: dict[str, dict[str, Any]] = {}

    query = select(Transaction).where(
        Transaction.status != TransactionStatus.CANCELLED,
        Transaction.status != TransactionStatus.FAILED,
    )

    if lookback_days > 0:
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
        query = query.where(Transaction.occurred_at >= since)

    if min_amount > 0:
        query = query.where(Transaction.amount >= min_amount)

    transactions = db.execute(query.order_by(Transaction.occurred_at.asc())).scalars().all()

    for tx in transactions:
        u = tx.sender_account_id
        v = tx.receiver_account_id
        amt = float(tx.amount)

        if not G.has_node(u):
            G.add_node(u, volume_in=0.0, volume_out=0.0)
        if not G.has_node(v):
            G.add_node(v, volume_in=0.0, volume_out=0.0)

        G.nodes[u]["volume_out"] += amt
        G.nodes[v]["volume_in"] += amt

        edge_key = f"{u}->{v}"
        if G.has_edge(u, v):
            G[u][v]["weight"] += amt
            G[u][v]["count"] += 1
            if tx.occurred_at and (
                G[u][v]["latest_time"] is None or tx.occurred_at > G[u][v]["latest_time"]
            ):
                G[u][v]["latest_time"] = tx.occurred_at
        else:
            G.add_edge(u, v, weight=amt, count=1, latest_time=tx.occurred_at)

        edge_metadata[edge_key] = {
            "source": u,
            "target": v,
            "weight": G[u][v]["weight"],
            "count": G[u][v]["count"],
            "latest_time": G[u][v]["latest_time"],
        }

    return G, edge_metadata


def detect_mule_cycles(
    G: nx.DiGraph,
    min_len: int = 3,
    max_len: int = 6,
    max_cycles: int = 500,
) -> tuple[dict[str, int], dict[str, str], int]:
    """
    Detects directed simple cycles (mule rings / layering loops) between min_len and max_len.
    Returns:
      - node_to_cycle_len: map from account_id to shortest cycle length
      - node_to_cycle_str: map from account_id to cycle representation string
      - total_cycles_found: count of detected valid cycles
    """
    node_to_cycle_len: dict[str, int] = {}
    node_to_cycle_str: dict[str, str] = {}
    cycles_found = 0

    if len(G) < min_len:
        return node_to_cycle_len, node_to_cycle_str, 0

    try:
        # nx.simple_cycles generates elementary directed circuits
        for cycle in nx.simple_cycles(G):
            c_len = len(cycle)
            if min_len <= c_len <= max_len:
                cycles_found += 1
                cycle_str = " -> ".join(cycle) + f" -> {cycle[0]}"
                for node in cycle:
                    if node not in node_to_cycle_len or c_len < node_to_cycle_len[node]:
                        node_to_cycle_len[node] = c_len
                        node_to_cycle_str[node] = cycle_str

                if cycles_found >= max_cycles:
                    break
    except Exception:
        # Fallback safely if graph algorithm encounters any error
        pass

    return node_to_cycle_len, node_to_cycle_str, cycles_found


def recalculate_network_risk(db: Session, lookback_days: int = 30) -> RecalculateNetworkResponse:
    """
    Executes full network topology analysis and persists/updates account_network_risk records.
    """
    G, _ = build_transaction_graph(db, lookback_days=lookback_days)
    now = datetime.now(timezone.utc)

    if len(G) == 0:
        return RecalculateNetworkResponse(
            accounts_analyzed=0,
            mule_suspects_detected=0,
            cycles_detected=0,
            hubs_detected=0,
            calculated_at=now,
        )

    # Compute PageRank
    try:
        pagerank_scores = nx.pagerank(G, weight="weight", alpha=0.85, max_iter=200)
    except Exception:
        pagerank_scores = {node: 1.0 / len(G) for node in G.nodes()}

    # Compute Mule Cycles (length 3 to 6)
    mule_cycle_lens, mule_cycle_strs, total_cycles = detect_mule_cycles(G, min_len=3, max_len=6)

    # Detect Strongly Connected Components for community clustering
    scc = list(nx.strongly_connected_components(G))
    node_community: dict[str, int] = {}
    for comm_idx, comp in enumerate(scc):
        for node in comp:
            node_community[node] = comm_idx

    mule_count = 0
    hubs_count = 0

    # Calculate metrics for each account
    for node in G.nodes():
        in_deg = G.in_degree(node)
        out_deg = G.out_degree(node)
        vol_in = G.nodes[node].get("volume_in", 0.0)
        vol_out = G.nodes[node].get("volume_out", 0.0)
        pr_score = pagerank_scores.get(node, 0.0)
        comm_id = node_community.get(node)

        fan_ratio = float(in_deg) / max(out_deg, 1) if out_deg > 0 else float(in_deg)

        # Risk classification
        is_mule = False
        cycle_len = None
        risk_reasons: list[str] = []
        flag = NetworkRiskFlag.NORMAL

        if node in mule_cycle_lens:
            is_mule = True
            cycle_len = mule_cycle_lens[node]
            flag = NetworkRiskFlag.MULE_CYCLE
            risk_reasons.append(f"Mule cycle detected (len={cycle_len}): {mule_cycle_strs[node]}")
            mule_count += 1
        elif in_deg >= 4 and (out_deg <= 1 or fan_ratio >= 4.0):
            flag = NetworkRiskFlag.FAN_IN_AGGREGATOR
            risk_reasons.append(f"Fan-in aggregator: {in_deg} senders, ratio {fan_ratio:.1f}")
            hubs_count += 1
        elif out_deg >= 4 and (in_deg <= 1 or (float(out_deg) / max(in_deg, 1)) >= 4.0):
            flag = NetworkRiskFlag.FAN_OUT_DISTRIBUTOR
            ratio_out = float(out_deg) / max(in_deg, 1)
            risk_reasons.append(f"Fan-out distributor: {out_deg} receivers, ratio {ratio_out:.1f}")
            hubs_count += 1
        elif pr_score > 0.05 and (in_deg + out_deg) >= 4:
            flag = NetworkRiskFlag.HIGH_CENTRALITY_HUB
            risk_reasons.append(f"High centrality hub: PageRank {pr_score:.4f}, degree {in_deg + out_deg}")
            hubs_count += 1

        reasons_text = "; ".join(risk_reasons) if risk_reasons else None

        # Upsert into account_network_risk
        existing = db.execute(
            select(AccountNetworkRisk).where(AccountNetworkRisk.account_id == node)
        ).scalar_one_or_none()

        if existing:
            existing.centrality_score = pr_score
            existing.in_degree = in_deg
            existing.out_degree = out_deg
            existing.fan_in_out_ratio = fan_ratio
            existing.total_volume_in = vol_in
            existing.total_volume_out = vol_out
            existing.is_mule_suspect = is_mule
            existing.cycle_length = cycle_len
            existing.community_id = comm_id
            existing.risk_flag = flag
            existing.risk_reasons = reasons_text
            existing.calculated_at = now
        else:
            new_record = AccountNetworkRisk(
                account_id=node,
                centrality_score=pr_score,
                in_degree=in_deg,
                out_degree=out_deg,
                fan_in_out_ratio=fan_ratio,
                total_volume_in=vol_in,
                total_volume_out=vol_out,
                is_mule_suspect=is_mule,
                cycle_length=cycle_len,
                community_id=comm_id,
                risk_flag=flag,
                risk_reasons=reasons_text,
                calculated_at=now,
            )
            db.add(new_record)

    db.commit()

    return RecalculateNetworkResponse(
        accounts_analyzed=len(G),
        mule_suspects_detected=mule_count,
        cycles_detected=total_cycles,
        hubs_detected=hubs_count,
        calculated_at=now,
    )


def get_account_network_risk(db: Session, account_id: str) -> AccountNetworkRisk | None:
    """Retrieve network risk metrics for a specific account."""
    return db.execute(
        select(AccountNetworkRisk).where(AccountNetworkRisk.account_id == account_id)
    ).scalar_one_or_none()


def list_suspicious_accounts(
    db: Session,
    skip: int = 0,
    limit: int = 50,
    flag: NetworkRiskFlag | None = None,
    mule_only: bool = False,
) -> tuple[list[AccountNetworkRisk], int]:
    """List suspicious accounts matching risk flags or mule status."""
    query = select(AccountNetworkRisk)
    count_query = select(func.count(AccountNetworkRisk.id))

    if mule_only:
        query = query.where(AccountNetworkRisk.is_mule_suspect == True)  # noqa: E712
        count_query = count_query.where(AccountNetworkRisk.is_mule_suspect == True)  # noqa: E712
    elif flag:
        query = query.where(AccountNetworkRisk.risk_flag == flag)
        count_query = count_query.where(AccountNetworkRisk.risk_flag == flag)
    else:
        query = query.where(AccountNetworkRisk.risk_flag != NetworkRiskFlag.NORMAL)
        count_query = count_query.where(AccountNetworkRisk.risk_flag != NetworkRiskFlag.NORMAL)

    total = db.execute(count_query).scalar_one()
    items = (
        db.execute(
            query.order_by(
                AccountNetworkRisk.is_mule_suspect.desc(),
                AccountNetworkRisk.centrality_score.desc(),
            )
            .offset(skip)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    return list(items), total


def get_network_graph(
    db: Session,
    min_amount: float = 0.0,
    limit: int = 100,
    account_id: str | None = None,
) -> NetworkGraphResponse:
    """
    Returns nodes and edges formatted for visualization.
    If account_id is provided, extracts the 1-hop neighborhood of that account.
    """
    G, edge_metadata = build_transaction_graph(db, lookback_days=30, min_amount=min_amount)

    target_nodes = set(G.nodes())
    if account_id and account_id in G:
        # 1-hop neighborhood: account + predecessors + successors
        target_nodes = {account_id} | set(G.predecessors(account_id)) | set(G.successors(account_id))

    # Fetch network risk for these nodes from DB
    risk_records = db.execute(
        select(AccountNetworkRisk).where(AccountNetworkRisk.account_id.in_(list(target_nodes)))
    ).scalars().all() if target_nodes else []

    risk_map = {r.account_id: r for r in risk_records}

    nodes: list[NetworkGraphNode] = []
    node_ids: set[str] = set()

    for node in list(target_nodes)[:limit]:
        node_ids.add(node)
        r = risk_map.get(node)
        vol = (G.nodes[node].get("volume_in", 0.0) + G.nodes[node].get("volume_out", 0.0))
        nodes.append(
            NetworkGraphNode(
                id=node,
                label=node,
                centrality=r.centrality_score if r else 0.0,
                in_degree=G.in_degree(node),
                out_degree=G.out_degree(node),
                is_mule=r.is_mule_suspect if r else False,
                risk_flag=r.risk_flag.value if r else "normal",
                total_volume=vol,
            )
        )

    edges: list[NetworkGraphEdge] = []
    edge_idx = 0
    for (u, v), data in G.edges.items():
        if u in node_ids and v in node_ids:
            edges.append(
                NetworkGraphEdge(
                    id=f"e-{edge_idx}",
                    source=u,
                    target=v,
                    amount=data["weight"],
                    count=data["count"],
                    timestamp=data.get("latest_time"),
                )
            )
            edge_idx += 1
            if len(edges) >= limit * 2:
                break

    # Count mule cycles
    mule_cycles_count = sum(1 for r in risk_records if r.is_mule_suspect)

    return NetworkGraphResponse(
        nodes=nodes,
        edges=edges,
        total_nodes=len(nodes),
        total_edges=len(edges),
        mule_cycles_count=mule_cycles_count,
    )
