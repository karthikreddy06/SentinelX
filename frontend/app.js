/**
 * SentinelX — Real-Time Fraud Intelligence Dashboard
 * Pure Vanilla JavaScript (Zero External Frameworks / Zero Dependencies)
 */

// Application State
const state = {
  token: sessionStorage.getItem("sx_token") || null,
  refreshToken: sessionStorage.getItem("sx_refresh") || null,
  user: JSON.parse(sessionStorage.getItem("sx_user") || "null"),
  activeTab: "tab-overview",
  graphData: { nodes: [], edges: [] },
  graphSimulation: null,
  canvasTransform: { x: 0, y: 0, scale: 1 },
  isDraggingCanvas: false,
  dragStart: { x: 0, y: 0 },
  selectedNode: null,
};

// --- AUTH & API INTERCEPTOR ---

function getAuthHeaders() {
  const headers = { "Content-Type": "application/json" };
  if (state.token) {
    headers["Authorization"] = `Bearer ${state.token}`;
  }
  return headers;
}

async function apiFetch(endpoint, options = {}) {
  const url = endpoint.startsWith("http") ? endpoint : endpoint;
  options.headers = { ...getAuthHeaders(), ...(options.headers || {}) };

  let res = await fetch(url, options);

  // Auto-refresh token if 401 Unauthorized
  if (res.status === 401 && state.refreshToken) {
    const refreshed = await attemptRefreshToken();
    if (refreshed) {
      options.headers["Authorization"] = `Bearer ${state.token}`;
      res = await fetch(url, options);
    } else {
      logout();
      showLoginModal();
      throw new Error("Session expired. Please sign in again.");
    }
  }

  if (!res.ok) {
    const errData = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(errData.detail || "API request failed");
  }

  return res.json();
}

async function attemptRefreshToken() {
  try {
    const res = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: state.refreshToken }),
    });
    if (!res.ok) return false;
    const data = await res.json();
    state.token = data.access_token;
    state.refreshToken = data.refresh_token;
    sessionStorage.setItem("sx_token", state.token);
    sessionStorage.setItem("sx_refresh", state.refreshToken);
    return true;
  } catch {
    return false;
  }
}

function updateAuthUI() {
  const loggedInDiv = document.getElementById("auth-logged-in");
  const loggedOutDiv = document.getElementById("auth-logged-out");
  const userEmailSpan = document.getElementById("user-email");
  const roleBadge = document.getElementById("user-role-badge");

  if (state.user && state.token) {
    loggedInDiv.style.display = "flex";
    loggedOutDiv.style.display = "none";
    userEmailSpan.textContent = state.user.email;
    roleBadge.textContent = state.user.role;
    roleBadge.className = `role-badge ${state.user.role.toLowerCase()}`;
  } else {
    loggedInDiv.style.display = "none";
    loggedOutDiv.style.display = "block";
  }
}

async function login(email, password) {
  const errDiv = document.getElementById("login-error");
  errDiv.style.display = "none";

  try {
    const res = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });

    const data = await res.json();
    if (!res.ok) {
      errDiv.textContent = data.detail || "Login failed";
      errDiv.style.display = "block";
      return;
    }

    state.token = data.access_token;
    state.refreshToken = data.refresh_token;
    sessionStorage.setItem("sx_token", state.token);
    sessionStorage.setItem("sx_refresh", state.refreshToken);

    // Fetch user details
    const me = await apiFetch("/api/v1/auth/me");
    state.user = me;
    sessionStorage.setItem("sx_user", JSON.stringify(me));

    updateAuthUI();
    closeLoginModal();
    refreshActiveTab();
  } catch (err) {
    errDiv.textContent = err.message;
    errDiv.style.display = "block";
  }
}

function logout() {
  state.token = null;
  state.refreshToken = null;
  state.user = null;
  sessionStorage.removeItem("sx_token");
  sessionStorage.removeItem("sx_refresh");
  sessionStorage.removeItem("sx_user");
  updateAuthUI();
}

// --- TAB ROUTING ---

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll(".nav-tab").forEach(tab => {
    tab.classList.toggle("active", tab.dataset.tab === tabId);
  });
  document.querySelectorAll(".tab-panel").forEach(panel => {
    panel.classList.toggle("active", panel.id === tabId);
  });
  refreshActiveTab();
}

function refreshActiveTab() {
  if (state.activeTab === "tab-overview") loadOverview();
  else if (state.activeTab === "tab-transactions") loadTransactions();
  else if (state.activeTab === "tab-alerts") loadAlerts();
  else if (state.activeTab === "tab-investigations") loadInvestigations();
  else if (state.activeTab === "tab-network") loadNetworkGraph();
}

// --- SYSTEM HEALTH MONITORING ---

async function checkSystemHealth() {
  try {
    const apiRes = await fetch("/health");
    setHealthDot("pill-api", apiRes.ok);

    const dbRes = await fetch("/health/database");
    setHealthDot("pill-db", dbRes.ok);

    const redisRes = await fetch("/health/redis").catch(() => null);
    if (redisRes && redisRes.ok) {
      const data = await redisRes.json();
      setHealthDot("pill-redis", data.redis_online);
    } else {
      setHealthDot("pill-redis", false);
    }

    const streamRes = await fetch("/health/streaming").catch(() => null);
    if (streamRes && streamRes.ok) {
      const sData = await streamRes.json();
      setHealthDot("pill-stream", sData.broker_online);
    } else {
      setHealthDot("pill-stream", false);
    }
  } catch {
    // Graceful silent fallback
  }
}

function setHealthDot(pillId, isOnline) {
  const pill = document.getElementById(pillId);
  if (!pill) return;
  const dot = pill.querySelector(".dot");
  if (dot) {
    dot.className = `dot ${isOnline ? "online" : "warning"}`;
  }
}

// --- TAB 1: EXECUTIVE OVERVIEW ---

async function loadOverview() {
  try {
    // 1. Transactions summary
    const txRes = await apiFetch("/api/v1/transactions?limit=100");
    const items = txRes.items || [];
    const totalVolume = items.reduce((sum, t) => sum + parseFloat(t.amount), 0);
    const highRiskCount = items.filter(t => t.risk_level === "high" || (t.risk_score && t.risk_score >= 70)).length;
    const fraudRate = items.length > 0 ? ((highRiskCount / items.length) * 100).toFixed(1) : "0.0";

    document.getElementById("kpi-volume").textContent = `$${totalVolume.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    document.getElementById("kpi-count").textContent = `${items.length} Transactions processed`;
    document.getElementById("kpi-fraud-rate").textContent = `${fraudRate}%`;
    document.getElementById("kpi-fraud-count").textContent = `${highRiskCount} Flagged high risk`;

    // 2. Alert stats
    const stats = await apiFetch("/api/v1/alerts/stats/summary").catch(() => ({ open_alerts: 0, critical_alerts: 0 }));
    document.getElementById("kpi-open-alerts").textContent = stats.open_alerts || 0;
    document.getElementById("kpi-critical-alerts").textContent = `${stats.critical_alerts || 0} Critical priority`;

    // 3. Network stats
    const netRes = await apiFetch("/api/v1/network/suspicious?mule_only=true").catch(() => ({ total: 0 }));
    document.getElementById("kpi-mule-rings").textContent = netRes.total || 0;

    // 4. Feeds
    renderOverviewAlerts();
    renderOverviewInvestigations();
  } catch (err) {
    console.error("Overview load error:", err);
  }
}

async function renderOverviewAlerts() {
  const container = document.getElementById("overview-recent-alerts");
  try {
    const res = await apiFetch("/api/v1/alerts?limit=5");
    const alerts = res.items || [];
    if (alerts.length === 0) {
      container.innerHTML = '<div class="empty-state">No active alerts recorded.</div>';
      return;
    }
    container.innerHTML = alerts.map(a => `
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 0.5rem; border-bottom: 1px solid var(--border-subtle);">
        <div>
          <span class="badge badge-${a.severity.toLowerCase()}">${a.severity}</span>
          <span style="font-size: 0.82rem; margin-left: 0.5rem; font-weight: 500;">Tx: ${a.transaction_id.slice(0, 8)}...</span>
        </div>
        <div style="font-size: 0.8rem; color: var(--text-muted);">Score: <strong>${a.risk_score}</strong></div>
      </div>
    `).join("");
  } catch {
    container.innerHTML = '<div class="empty-state">Sign in to view alerts.</div>';
  }
}

async function renderOverviewInvestigations() {
  const container = document.getElementById("overview-recent-investigations");
  try {
    const res = await apiFetch("/api/v1/investigations?limit=5");
    const invs = res.items || [];
    if (invs.length === 0) {
      container.innerHTML = '<div class="empty-state">No open investigations.</div>';
      return;
    }
    container.innerHTML = invs.map(i => `
      <div style="display: flex; justify-content: space-between; align-items: center; padding: 0.65rem 0.5rem; border-bottom: 1px solid var(--border-subtle);">
        <div>
          <span class="badge badge-${i.priority.toLowerCase()}">${i.priority}</span>
          <span style="font-size: 0.82rem; margin-left: 0.5rem;">${i.title}</span>
        </div>
        <span class="badge" style="background: var(--bg-tertiary);">${i.status}</span>
      </div>
    `).join("");
  } catch {
    container.innerHTML = '<div class="empty-state">Sign in to view investigations.</div>';
  }
}

// --- TAB 2: TRANSACTIONS EXPLORER ---

async function loadTransactions() {
  const tbody = document.getElementById("tx-table-body");
  const search = document.getElementById("tx-search-input").value.trim();
  const risk = document.getElementById("tx-risk-filter").value;
  const status = document.getElementById("tx-status-filter").value;

  tbody.innerHTML = '<tr><td colspan="9" class="empty-state">Loading transactions...</td></tr>';

  let query = "/api/v1/transactions?limit=100";
  if (risk) query += `&risk_level=${risk}`;
  if (status) query += `&status=${status}`;
  if (search) query += `&sender_account_id=${encodeURIComponent(search)}`;

  try {
    const res = await apiFetch(query);
    const items = res.items || [];
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="9" class="empty-state">No matching transactions found.</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(tx => {
      const riskBadge = tx.risk_level
        ? `<span class="badge badge-${tx.risk_level}">${tx.risk_level}</span>`
        : '<span class="badge" style="background: var(--bg-tertiary);">Pending</span>';
      const score = tx.risk_score !== null ? tx.risk_score : "-";
      const timeStr = new Date(tx.occurred_at).toLocaleTimeString();

      return `
        <tr onclick="inspectTransaction('${tx.id}')">
          <td style="color: var(--text-muted); font-size: 0.78rem;">${timeStr}</td>
          <td style="font-family: monospace; font-size: 0.78rem;">${tx.id.slice(0, 8)}...</td>
          <td style="font-weight: 500;">${tx.sender_account_id}</td>
          <td style="font-weight: 500;">${tx.receiver_account_id}</td>
          <td style="font-weight: 600;">$${parseFloat(tx.amount).toFixed(2)}</td>
          <td><span class="badge" style="background: var(--bg-tertiary);">${tx.status}</span></td>
          <td style="font-weight: 700;">${score}</td>
          <td>${riskBadge}</td>
          <td><button class="btn" style="padding: 0.2rem 0.5rem;" onclick="event.stopPropagation(); inspectTransaction('${tx.id}')">Inspect</button></td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="9" class="empty-state" style="color: var(--accent-rose);">${err.message}</td></tr>`;
  }
}

// Drawer: Inspect Transaction
async function inspectTransaction(txId) {
  openDrawer("Transaction Intelligence Dossier");
  const content = document.getElementById("drawer-content");
  content.innerHTML = '<div class="empty-state">Retrieving risk analysis dossier...</div>';

  try {
    const tx = await apiFetch(`/api/v1/transactions/${txId}`);
    const risk = await apiFetch(`/api/v1/transactions/${txId}/risk`).catch(() => null);

    const score = tx.risk_score !== null ? tx.risk_score : "Not Scored";
    const riskLevel = tx.risk_level || "pending";

    let reasonsHtml = '<div style="color: var(--text-muted); font-size: 0.85rem;">No rules triggered. Clean transaction.</div>';
    if (risk && risk.length > 0 && risk[0].reasons && risk[0].reasons.length > 0) {
      reasonsHtml = risk[0].reasons.map(r => `
        <div class="rule-pill ${r.triggered ? 'triggered' : ''}">
          <div style="display: flex; justify-content: space-between; font-weight: 600; margin-bottom: 0.25rem;">
            <span>${r.rule_name} [${r.reason_code}]</span>
            <span style="color: var(--accent-rose);">+${r.score_contribution} pts</span>
          </div>
          <div style="font-size: 0.78rem; color: var(--text-secondary); margin-bottom: 0.35rem;">${r.description}</div>
          <pre style="font-size: 0.7rem; background: var(--bg-primary); padding: 0.4rem; border-radius: 4px; overflow-x: auto;">${JSON.stringify(r.evidence, null, 2)}</pre>
        </div>
      `).join("");
    }

    content.innerHTML = `
      <div class="detail-section">
        <h4>Transaction Core Information</h4>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.5rem; font-size: 0.82rem;">
          <div><span style="color: var(--text-muted);">ID:</span> ${tx.id}</div>
          <div><span style="color: var(--text-muted);">Amount:</span> $${parseFloat(tx.amount).toFixed(2)} ${tx.currency}</div>
          <div><span style="color: var(--text-muted);">Sender:</span> <strong>${tx.sender_account_id}</strong></div>
          <div><span style="color: var(--text-muted);">Receiver:</span> <strong>${tx.receiver_account_id}</strong></div>
          <div><span style="color: var(--text-muted);">Occurred:</span> ${new Date(tx.occurred_at).toLocaleString()}</div>
          <div><span style="color: var(--text-muted);">Status:</span> ${tx.status}</div>
        </div>
      </div>

      <div class="detail-section">
        <h4>Risk Assessment Engine</h4>
        <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 0.75rem;">
          <div>
            <div style="font-size: 1.8rem; font-weight: 800; color: ${score >= 70 ? 'var(--accent-rose)' : (score >= 30 ? 'var(--accent-amber)' : 'var(--accent-emerald)')};">${score} / 100</div>
            <div style="font-size: 0.78rem; color: var(--text-muted);">Hybrid Score (60% Rules + 40% ML)</div>
          </div>
          <span class="badge badge-${riskLevel}" style="font-size: 0.85rem; padding: 0.35rem 0.8rem;">${riskLevel}</span>
        </div>
        <button class="btn btn-primary" style="width: 100%; margin-top: 0.5rem;" onclick="rescoreTransaction('${tx.id}')">Trigger On-Demand Rescore</button>
      </div>

      <div class="detail-section">
        <h4>Rule Evaluations & Point-in-Time Evidence</h4>
        ${reasonsHtml}
      </div>
    `;
  } catch (err) {
    content.innerHTML = `<div class="empty-state" style="color: var(--accent-rose);">${err.message}</div>`;
  }
}

async function rescoreTransaction(txId) {
  try {
    await apiFetch(`/api/v1/transactions/${txId}/score`, { method: "POST" });
    inspectTransaction(txId);
    loadTransactions();
  } catch (err) {
    alert("Rescore failed: " + err.message);
  }
}

// --- TAB 3: ALERTS TRIAGE ---

async function loadAlerts() {
  const tbody = document.getElementById("alerts-table-body");
  const status = document.getElementById("alert-status-filter").value;
  const severity = document.getElementById("alert-severity-filter").value;

  tbody.innerHTML = '<tr><td colspan="8" class="empty-state">Loading alerts...</td></tr>';

  let query = "/api/v1/alerts?limit=100";
  if (status) query += `&status=${status}`;
  if (severity) query += `&severity=${severity}`;

  try {
    const res = await apiFetch(query);
    const items = res.items || [];
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="8" class="empty-state">No alerts matching filter criteria.</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(a => {
      const reasons = a.reason_codes ? a.reason_codes.join(", ") : "-";
      return `
        <tr>
          <td><span class="badge badge-${a.severity.toLowerCase()}">${a.severity}</span></td>
          <td style="font-family: monospace; font-size: 0.78rem;">${a.id.slice(0, 8)}...</td>
          <td style="font-family: monospace; font-size: 0.78rem;">${a.transaction_id.slice(0, 8)}...</td>
          <td style="font-weight: 700;">${a.risk_score}</td>
          <td style="font-size: 0.78rem; color: var(--text-secondary);">${reasons}</td>
          <td><span class="badge" style="background: var(--bg-tertiary);">${a.status}</span></td>
          <td style="color: var(--text-muted); font-size: 0.78rem;">${new Date(a.created_at).toLocaleTimeString()}</td>
          <td>
            <div style="display: flex; gap: 0.35rem;">
              ${a.status === 'open' ? `<button class="btn" style="padding: 0.2rem 0.5rem;" onclick="acknowledgeAlert('${a.id}')">Acknowledge</button>` : ''}
              ${a.status !== 'resolved' && a.status !== 'dismissed' ? `<button class="btn btn-primary" style="padding: 0.2rem 0.5rem;" onclick="openCreateInvModalForAlert('${a.id}')">Investigate</button>` : ''}
              ${a.status !== 'resolved' && a.status !== 'dismissed' ? `<button class="btn btn-danger" style="padding: 0.2rem 0.5rem;" onclick="dismissAlertPrompt('${a.id}')">Dismiss</button>` : ''}
            </div>
          </td>
        </tr>
      `;
    }).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="8" class="empty-state" style="color: var(--accent-rose);">${err.message}</td></tr>`;
  }
}

async function acknowledgeAlert(alertId) {
  try {
    await apiFetch(`/api/v1/alerts/${alertId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "investigating" }),
    });
    loadAlerts();
  } catch (err) {
    alert("Action failed: " + err.message);
  }
}

async function dismissAlertPrompt(alertId) {
  const reason = prompt("Enter dismissal reason (e.g. Verified legitimate business transfer):");
  if (!reason) return;
  try {
    await apiFetch(`/api/v1/alerts/${alertId}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "dismissed", resolution_notes: reason }),
    });
    loadAlerts();
  } catch (err) {
    alert("Dismissal failed: " + err.message);
  }
}

// --- TAB 4: INVESTIGATIONS CASE MANAGER ---

async function loadInvestigations() {
  const tbody = document.getElementById("inv-table-body");
  const status = document.getElementById("inv-status-filter").value;

  tbody.innerHTML = '<tr><td colspan="7" class="empty-state">Loading investigations...</td></tr>';

  let query = "/api/v1/investigations?limit=100";
  if (status) query += `&status=${status}`;

  try {
    const res = await apiFetch(query);
    const items = res.items || [];
    if (items.length === 0) {
      tbody.innerHTML = '<tr><td colspan="7" class="empty-state">No investigation cases recorded.</td></tr>';
      return;
    }

    tbody.innerHTML = items.map(inv => `
      <tr onclick="inspectInvestigation('${inv.id}')">
        <td><span class="badge badge-${inv.priority.toLowerCase()}">${inv.priority}</span></td>
        <td style="font-weight: 600;">${inv.title}</td>
        <td><span class="badge" style="background: var(--bg-tertiary);">${inv.status}</span></td>
        <td style="font-size: 0.8rem; color: var(--text-secondary);">${inv.assigned_to_id ? 'Assigned' : 'Unassigned'}</td>
        <td style="font-weight: 500;">${inv.alert_count || 1}</td>
        <td style="color: var(--text-muted); font-size: 0.78rem;">${new Date(inv.created_at).toLocaleDateString()}</td>
        <td><button class="btn" style="padding: 0.2rem 0.5rem;" onclick="event.stopPropagation(); inspectInvestigation('${inv.id}')">View Case</button></td>
      </tr>
    `).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" class="empty-state" style="color: var(--accent-rose);">${err.message}</td></tr>`;
  }
}

async function inspectInvestigation(invId) {
  openDrawer("Investigation Case Dossier");
  const content = document.getElementById("drawer-content");
  content.innerHTML = '<div class="empty-state">Loading case details...</div>';

  try {
    const inv = await apiFetch(`/api/v1/investigations/${invId}`);

    const notesHtml = (inv.notes && inv.notes.length > 0)
      ? inv.notes.map(n => `
          <div style="background: var(--bg-tertiary); padding: 0.6rem; border-radius: 4px; margin-bottom: 0.5rem; font-size: 0.8rem;">
            <div style="color: var(--text-muted); font-size: 0.72rem; margin-bottom: 0.2rem;">${new Date(n.created_at).toLocaleString()}</div>
            <div>${n.body}</div>
          </div>
        `).join("")
      : '<div style="color: var(--text-muted); font-size: 0.8rem;">No notes recorded yet.</div>';

    const evidenceHtml = (inv.evidence && inv.evidence.length > 0)
      ? inv.evidence.map(e => `
          <div style="background: var(--bg-tertiary); padding: 0.6rem; border-radius: 4px; margin-bottom: 0.5rem; font-size: 0.8rem;">
            <div style="font-weight: 600; color: var(--accent-cyan);">${e.evidence_type}</div>
            <div style="font-size: 0.75rem; color: var(--text-muted);">${e.description || ''}</div>
          </div>
        `).join("")
      : '<div style="color: var(--text-muted); font-size: 0.8rem;">No evidence attached yet.</div>';

    content.innerHTML = `
      <div class="detail-section">
        <h4>Case Overview</h4>
        <h3 style="font-size: 1.1rem; margin-bottom: 0.5rem;">${inv.title}</h3>
        <p style="font-size: 0.85rem; color: var(--text-secondary); margin-bottom: 0.75rem;">${inv.description || 'No description provided.'}</p>
        <div style="display: flex; gap: 0.5rem;">
          <span class="badge badge-${inv.priority.toLowerCase()}">${inv.priority}</span>
          <span class="badge" style="background: var(--bg-tertiary);">${inv.status}</span>
        </div>
      </div>

      <div class="detail-section">
        <h4>Add Investigation Note</h4>
        <textarea id="drawer-note-input" rows="2" style="width: 100%; margin-bottom: 0.5rem;" class="form-group input" placeholder="Enter findings, customer outreach notes..."></textarea>
        <button class="btn btn-primary" onclick="submitInvestigationNote('${inv.id}')">Post Note</button>
      </div>

      <div class="detail-section">
        <h4>Notes Timeline</h4>
        ${notesHtml}
      </div>

      <div class="detail-section">
        <h4>Attached Evidence</h4>
        ${evidenceHtml}
      </div>

      ${inv.status !== 'closed' ? `
        <div class="detail-section">
          <h4>Close Case Outcome</h4>
          <div style="display: flex; gap: 0.5rem;">
            <button class="btn btn-danger" onclick="closeCaseOutcome('${inv.id}', 'fraud_confirmed')">Confirm Fraud</button>
            <button class="btn" onclick="closeCaseOutcome('${inv.id}', 'false_positive')">False Positive</button>
            <button class="btn" onclick="closeCaseOutcome('${inv.id}', 'inconclusive')">Inconclusive</button>
          </div>
        </div>
      ` : ''}
    `;
  } catch (err) {
    content.innerHTML = `<div class="empty-state" style="color: var(--accent-rose);">${err.message}</div>`;
  }
}

async function submitInvestigationNote(invId) {
  const input = document.getElementById("drawer-note-input");
  const note = input.value.trim();
  if (!note) return;
  try {
    await apiFetch(`/api/v1/investigations/${invId}/notes`, {
      method: "POST",
      body: JSON.stringify({ body: note }),
    });
    inspectInvestigation(invId);
  } catch (err) {
    alert("Note failed: " + err.message);
  }
}

async function closeCaseOutcome(invId, outcome) {
  const reason = prompt(`Provide closing rationale for [${outcome}]:`);
  if (!reason) return;
  try {
    await apiFetch(`/api/v1/investigations/${invId}`, {
      method: "PATCH",
      body: JSON.stringify({
        status: "closed",
        outcome: outcome,
        outcome_reason: reason,
      }),
    });
    inspectInvestigation(invId);
    loadInvestigations();
  } catch (err) {
    alert("Closure failed: " + err.message);
  }
}

// --- TAB 5: FRAUD NETWORK GRAPH (CANVAS) ---

async function loadNetworkGraph() {
  const accountId = document.getElementById("graph-account-filter").value.trim();
  const minAmount = document.getElementById("graph-min-amount").value || 0;

  let query = `/api/v1/network/graph?min_amount=${minAmount}&limit=120`;
  if (accountId) query += `&account_id=${encodeURIComponent(accountId)}`;

  try {
    const data = await apiFetch(query);
    state.graphData = data;
    initCanvasGraph();
  } catch (err) {
    console.error("Graph load error:", err);
  }
}

function initCanvasGraph() {
  const canvas = document.getElementById("networkCanvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");

  // Resize canvas to match display size
  const rect = canvas.parentElement.getBoundingClientRect();
  canvas.width = rect.width;
  canvas.height = rect.height;

  const nodes = state.graphData.nodes.map((n, i) => {
    // Initial circular layout
    const angle = (i / state.graphData.nodes.length) * Math.PI * 2;
    const r = Math.min(canvas.width, canvas.height) * 0.35;
    return {
      ...n,
      x: canvas.width / 2 + Math.cos(angle) * r + (Math.random() - 0.5) * 50,
      y: canvas.height / 2 + Math.sin(angle) * r + (Math.random() - 0.5) * 50,
      vx: 0,
      vy: 0,
      radius: n.is_mule ? 14 : (n.risk_flag !== 'normal' ? 12 : 9),
    };
  });

  const nodeMap = new Map();
  nodes.forEach(n => nodeMap.set(n.id, n));

  const edges = state.graphData.edges.map(e => ({
    ...e,
    sourceNode: nodeMap.get(e.source),
    targetNode: nodeMap.get(e.target),
  })).filter(e => e.sourceNode && e.targetNode);

  // Force-directed simulation parameters
  let iterations = 0;
  const maxIterations = 250;

  function stepSimulation() {
    if (iterations >= maxIterations) return;
    iterations++;

    // Repulsion
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[j].x - nodes[i].x;
        const dy = nodes[j].y - nodes[i].y;
        const dist = Math.sqrt(dx * dx + dy * dy) || 1;
        if (dist < 220) {
          const force = 1200 / (dist * dist);
          nodes[i].vx -= (dx / dist) * force;
          nodes[i].vy -= (dy / dist) * force;
          nodes[j].vx += (dx / dist) * force;
          nodes[j].vy += (dy / dist) * force;
        }
      }
    }

    // Spring attraction along edges
    edges.forEach(e => {
      const dx = e.targetNode.x - e.sourceNode.x;
      const dy = e.targetNode.y - e.sourceNode.y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 1;
      const desiredDist = 110;
      const force = (dist - desiredDist) * 0.04;
      e.sourceNode.vx += (dx / dist) * force;
      e.sourceNode.vy += (dy / dist) * force;
      e.targetNode.vx -= (dx / dist) * force;
      e.targetNode.vy -= (dy / dist) * force;
    });

    // Centering force & update
    nodes.forEach(n => {
      n.vx += (canvas.width / 2 - n.x) * 0.005;
      n.vy += (canvas.height / 2 - n.y) * 0.005;
      n.x += n.vx * 0.6;
      n.y += n.vy * 0.6;
      n.vx *= 0.85;
      n.vy *= 0.85;
    });
  }

  function render() {
    stepSimulation();
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(state.canvasTransform.x, state.canvasTransform.y);
    ctx.scale(state.canvasTransform.scale, state.canvasTransform.scale);

    // Draw edges
    edges.forEach(e => {
      ctx.beginPath();
      ctx.moveTo(e.sourceNode.x, e.sourceNode.y);
      ctx.lineTo(e.targetNode.x, e.targetNode.y);
      ctx.strokeStyle = "rgba(75, 85, 99, 0.45)";
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Draw directional arrow
      const angle = Math.atan2(e.targetNode.y - e.sourceNode.y, e.targetNode.x - e.sourceNode.x);
      const arrowX = e.targetNode.x - Math.cos(angle) * (e.targetNode.radius + 6);
      const arrowY = e.targetNode.y - Math.sin(angle) * (e.targetNode.radius + 6);
      ctx.beginPath();
      ctx.moveTo(arrowX, arrowY);
      ctx.lineTo(arrowX - 8 * Math.cos(angle - Math.PI / 6), arrowY - 8 * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(arrowX - 8 * Math.cos(angle + Math.PI / 6), arrowY - 8 * Math.sin(angle + Math.PI / 6));
      ctx.fillStyle = "rgba(156, 163, 175, 0.6)";
      ctx.fill();
    });

    // Draw nodes
    nodes.forEach(n => {
      ctx.beginPath();
      ctx.arc(n.x, n.y, n.radius, 0, Math.PI * 2);

      if (n.is_mule) {
        ctx.fillStyle = "#ef4444";
        ctx.shadowColor = "rgba(239, 68, 68, 0.6)";
        ctx.shadowBlur = 12;
      } else if (n.risk_flag !== "normal") {
        ctx.fillStyle = "#f59e0b";
        ctx.shadowColor = "rgba(245, 158, 11, 0.5)";
        ctx.shadowBlur = 8;
      } else {
        ctx.fillStyle = "#10b981";
        ctx.shadowBlur = 0;
      }

      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#111827";
      ctx.stroke();

      // Node label
      ctx.shadowBlur = 0;
      ctx.font = "10px -apple-system, sans-serif";
      ctx.fillStyle = "#d1d5db";
      ctx.textAlign = "center";
      ctx.fillText(n.id, n.x, n.y + n.radius + 12);
    });

    ctx.restore();
    requestAnimationFrame(render);
  }

  render();

  // Canvas Mouse Interactions
  canvas.onmousedown = (e) => {
    state.isDraggingCanvas = true;
    state.dragStart = { x: e.clientX - state.canvasTransform.x, y: e.clientY - state.canvasTransform.y };
  };

  canvas.onmousemove = (e) => {
    if (state.isDraggingCanvas) {
      state.canvasTransform.x = e.clientX - state.dragStart.x;
      state.canvasTransform.y = e.clientY - state.dragStart.y;
    }
  };

  window.onmouseup = () => {
    state.isDraggingCanvas = false;
  };

  canvas.onwheel = (e) => {
    e.preventDefault();
    const zoomFactor = e.deltaY < 0 ? 1.1 : 0.9;
    state.canvasTransform.scale = Math.min(Math.max(0.3, state.canvasTransform.scale * zoomFactor), 3);
  };

  canvas.onclick = (e) => {
    const rect = canvas.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left - state.canvasTransform.x) / state.canvasTransform.scale;
    const mouseY = (e.clientY - rect.top - state.canvasTransform.y) / state.canvasTransform.scale;

    for (const n of nodes) {
      const dx = mouseX - n.x;
      const dy = mouseY - n.y;
      if (dx * dx + dy * dy <= n.radius * n.radius) {
        inspectAccountNetwork(n.id);
        break;
      }
    }
  };
}

async function inspectAccountNetwork(accountId) {
  openDrawer(`Account Network Risk: ${accountId}`);
  const content = document.getElementById("drawer-content");
  content.innerHTML = '<div class="empty-state">Loading account network metrics...</div>';

  try {
    const net = await apiFetch(`/api/v1/network/accounts/${encodeURIComponent(accountId)}`);
    content.innerHTML = `
      <div class="detail-section">
        <h4>Topological Role & Classification</h4>
        <div style="margin-bottom: 0.5rem;">
          <span class="badge ${net.is_mule_suspect ? 'badge-critical' : 'badge-low'}">${net.risk_flag}</span>
        </div>
        <p style="font-size: 0.85rem; color: var(--accent-rose);">${net.risk_reasons || 'No topological risk detected.'}</p>
      </div>

      <div class="detail-section">
        <h4>Graph Metrics</h4>
        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; font-size: 0.82rem;">
          <div><span style="color: var(--text-muted);">In-Degree:</span> <strong>${net.in_degree}</strong> senders</div>
          <div><span style="color: var(--text-muted);">Out-Degree:</span> <strong>${net.out_degree}</strong> receivers</div>
          <div><span style="color: var(--text-muted);">Volume In:</span> $${parseFloat(net.total_volume_in).toFixed(2)}</div>
          <div><span style="color: var(--text-muted);">Volume Out:</span> $${parseFloat(net.total_volume_out).toFixed(2)}</div>
          <div><span style="color: var(--text-muted);">PageRank:</span> ${net.centrality_score.toFixed(4)}</div>
          <div><span style="color: var(--text-muted);">Cycle Length:</span> ${net.cycle_length || 'None'}</div>
        </div>
      </div>

      <div class="detail-section">
        <button class="btn btn-primary" style="width: 100%;" onclick="focusAccountInTransactions('${accountId}')">View Account Transactions</button>
      </div>
    `;
  } catch (err) {
    content.innerHTML = `<div class="empty-state" style="color: var(--accent-rose);">${err.message}</div>`;
  }
}

function focusAccountInTransactions(accountId) {
  closeDrawer();
  document.getElementById("tx-search-input").value = accountId;
  switchTab("tab-transactions");
}

// Recalculate Network Topology
async function triggerNetworkRecalculate() {
  if (!confirm("Execute full graph topology recalculation, PageRank, and mule cycle detection?")) return;
  try {
    const res = await apiFetch("/api/v1/network/recalculate", { method: "POST" });
    alert(`Recalculation complete!\nAnalyzed: ${res.accounts_analyzed} accounts\nMule suspects: ${res.mule_suspects_detected}\nCycles: ${res.cycles_detected}\nHubs: ${res.hubs_detected}`);
    loadNetworkGraph();
  } catch (err) {
    alert("Recalculation error: " + err.message);
  }
}

// --- DRAWER & MODAL UTILITIES ---

function openDrawer(title) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("drawer-overlay").classList.add("active");
  document.getElementById("detail-drawer").classList.add("open");
}

function closeDrawer() {
  document.getElementById("drawer-overlay").classList.remove("active");
  document.getElementById("detail-drawer").classList.remove("open");
}

function showLoginModal() {
  document.getElementById("login-modal").classList.add("active");
}

function closeLoginModal() {
  document.getElementById("login-modal").classList.remove("active");
}

function openCreateInvModalForAlert(alertId) {
  document.getElementById("inv-alert-ids").value = alertId || "";
  document.getElementById("inv-modal").classList.add("active");
}

function closeCreateInvModal() {
  document.getElementById("inv-modal").classList.remove("active");
}

// --- EVENT LISTENERS INITIALIZATION ---

document.addEventListener("DOMContentLoaded", () => {
  updateAuthUI();
  checkSystemHealth();
  refreshActiveTab();

  // Tab navigation
  document.querySelectorAll(".nav-tab").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Health poll every 15 seconds
  setInterval(checkSystemHealth, 15000);

  // Auth UI
  document.getElementById("btn-open-login").addEventListener("click", showLoginModal);
  document.getElementById("btn-close-login").addEventListener("click", closeLoginModal);
  document.getElementById("btn-logout").addEventListener("click", logout);

  document.getElementById("login-form").addEventListener("submit", (e) => {
    e.preventDefault();
    login(
      document.getElementById("login-email").value.trim(),
      document.getElementById("login-password").value
    );
  });

  // Drawer
  document.getElementById("drawer-close-btn").addEventListener("click", closeDrawer);
  document.getElementById("drawer-overlay").addEventListener("click", closeDrawer);

  // Filters & buttons
  document.getElementById("btn-refresh-tx").addEventListener("click", loadTransactions);
  document.getElementById("tx-search-input").addEventListener("keyup", (e) => {
    if (e.key === "Enter") loadTransactions();
  });
  document.getElementById("tx-risk-filter").addEventListener("change", loadTransactions);
  document.getElementById("tx-status-filter").addEventListener("change", loadTransactions);

  document.getElementById("btn-refresh-alerts").addEventListener("click", loadAlerts);
  document.getElementById("alert-status-filter").addEventListener("change", loadAlerts);
  document.getElementById("alert-severity-filter").addEventListener("change", loadAlerts);

  document.getElementById("btn-refresh-inv").addEventListener("click", loadInvestigations);
  document.getElementById("inv-status-filter").addEventListener("change", loadInvestigations);
  document.getElementById("btn-create-inv").addEventListener("click", () => openCreateInvModalForAlert(""));
  document.getElementById("btn-close-inv-modal").addEventListener("click", closeCreateInvModal);
  document.getElementById("btn-cancel-inv").addEventListener("click", closeCreateInvModal);

  document.getElementById("create-inv-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const title = document.getElementById("inv-title").value.trim();
    const description = document.getElementById("inv-description").value.trim();
    const priority = document.getElementById("inv-priority").value;
    const alertId = document.getElementById("inv-alert-ids").value.trim();

    try {
      const payload = { title, description, priority };
      if (alertId) payload.alert_ids = [alertId];
      await apiFetch("/api/v1/investigations", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      closeCreateInvModal();
      loadInvestigations();
    } catch (err) {
      alert("Failed to create case: " + err.message);
    }
  });

  // Network Controls
  document.getElementById("btn-load-graph").addEventListener("click", loadNetworkGraph);
  document.getElementById("btn-reset-graph").addEventListener("click", () => {
    state.canvasTransform = { x: 0, y: 0, scale: 1 };
    document.getElementById("graph-account-filter").value = "";
    document.getElementById("graph-min-amount").value = 0;
    loadNetworkGraph();
  });
  document.getElementById("btn-recalc-network").addEventListener("click", triggerNetworkRecalculate);
});
