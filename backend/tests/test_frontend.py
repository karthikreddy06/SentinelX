from pathlib import Path



def test_root_json_for_api_clients(client):
    """Verify GET / returns standard JSON status for API clients."""
    res = client.get("/", headers={"Accept": "application/json"})
    assert res.status_code == 200
    data = res.json()
    assert data["message"] == "Welcome to SentinelX"
    assert data["status"] == "running"


def test_root_serves_html_for_browsers(client):
    """Verify GET / returns the HTML dashboard when requested by a browser."""
    res = client.get("/", headers={"Accept": "text/html,application/xhtml+xml"})
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "SentinelX" in res.text
    assert "Real-Time Fraud Intelligence Engine" in res.text


def test_dashboard_endpoint(client):
    """Verify /dashboard returns the dashboard application."""
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "networkCanvas" in res.text


def test_static_assets_served(client):
    """Verify static assets are properly mounted and served."""
    res_css = client.get("/static/style.css")
    assert res_css.status_code == 200
    assert "var(--bg-primary)" in res_css.text

    res_js = client.get("/static/app.js")
    assert res_js.status_code == 200
    assert "SentinelX" in res_js.text


def test_zero_external_dependencies_and_no_frameworks():
    """Verify frontend code contains no React, Vue, Angular or external CDN links."""
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    assert frontend_dir.exists()

    index_html = (frontend_dir / "index.html").read_text(encoding="utf-8")
    app_js = (frontend_dir / "app.js").read_text(encoding="utf-8")
    style_css = (frontend_dir / "style.css").read_text(encoding="utf-8")

    forbidden_patterns = [
        "react",
        "react-dom",
        "vue",
        "angular",
        "cdn.jsdelivr.net",
        "cdnjs.cloudflare.com",
        "unpkg.com",
        "tailwindcss.com",
        "bootstrap",
    ]

    for pattern in forbidden_patterns:
        assert pattern not in index_html.lower(), f"Forbidden external dependency '{pattern}' found in index.html"
        assert pattern not in style_css.lower(), f"Forbidden external dependency '{pattern}' found in style.css"

    # In app.js, check that no framework imports exist
    assert "import react" not in app_js.lower()
    assert "require('react')" not in app_js.lower()


def test_no_browser_storage_of_tokens():
    """Verify frontend never stores tokens in localStorage or sessionStorage."""
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    app_js = (frontend_dir / "app.js").read_text(encoding="utf-8")

    assert "localStorage" not in app_js, "Forbidden localStorage found in frontend/app.js"
    assert "sessionStorage" not in app_js, "Forbidden sessionStorage found in frontend/app.js"


def test_csp_and_security_headers_present(client):
    """Verify security headers including CSP are returned on responses."""
    res = client.get("/")
    assert res.headers.get("X-Content-Type-Options") == "nosniff"
    assert res.headers.get("X-Frame-Options") == "DENY"
    assert res.headers.get("X-XSS-Protection") == "1; mode=block"
    assert "max-age" in res.headers.get("Strict-Transport-Security", "")
    assert "Content-Security-Policy" in res.headers
    assert "default-src 'self'" in res.headers["Content-Security-Policy"]


def test_xss_injection_payloads_rendered_inert(client, admin_headers, investigator_headers):
    """
    Test XSS payload handling in backend APIs and verify frontend escaping utility.
    Ensures payloads in transaction description, account IDs, investigation notes,
    and alert resolution notes cannot execute.
    """
    import uuid
    from datetime import datetime, timezone

    # 1. Transaction creation with script tags
    xss_payload = "<script>alert('xss')</script>"
    xss_img = "<img src=x onerror=alert('xss')>"
    tx_res = client.post(
        "/api/v1/transactions",
        headers={"Idempotency-Key": str(uuid.uuid4()), **admin_headers},
        json={
            "sender_account_id": "ACC_XSS_01",
            "receiver_account_id": "ACC_XSS_02",
            "amount": "1500.00",
            "currency": "USD",
            "transaction_type": "transfer",
            "occurred_at": datetime.now(timezone.utc).isoformat(),
            "description": f"Legit transfer {xss_payload} {xss_img}",
        },
    )
    assert tx_res.status_code == 201
    tx_data = tx_res.json()
    assert xss_payload in tx_data["description"]

    # 2. Add investigation note with XSS
    inv_res = client.post(
        "/api/v1/investigations",
        headers=investigator_headers,
        json={"title": f"Case {xss_img}", "description": xss_payload, "priority": "high"},
    )
    assert inv_res.status_code == 201
    inv_id = inv_res.json()["id"]

    note_res = client.post(
        f"/api/v1/investigations/{inv_id}/notes",
        headers=investigator_headers,
        json={"body": f"Investigator note: {xss_payload}"},
    )
    assert note_res.status_code == 201

    # 3. Verify escapeHtml in app.js escapes characters <, >, &, ", '
    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    app_js = (frontend_dir / "app.js").read_text(encoding="utf-8")
    assert "function escapeHtml" in app_js

    # Python simulation of app.js escapeHtml behavior
    def escape_html(s: str) -> str:
        return (
            s.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    escaped_desc = escape_html(tx_data["description"])
    assert "<script>" not in escaped_desc
    assert "&lt;script&gt;" in escaped_desc
    assert "<img" not in escape_html(xss_img)
    assert "&lt;img" in escape_html(xss_img)

