from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from app.main import app


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

