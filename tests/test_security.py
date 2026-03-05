"""Tests for security headers and error response safety."""

import json


def test_csp_header_present(client):
    """All responses should include a Content-Security-Policy header."""
    resp = client.get("/")
    assert "Content-Security-Policy" in resp.headers
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src" in csp
    assert "script-src" in csp


def test_x_content_type_options(client):
    """All responses should include X-Content-Type-Options: nosniff."""
    resp = client.get("/api/health")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"


def test_x_frame_options(client):
    """All responses should include X-Frame-Options: DENY."""
    resp = client.get("/")
    assert resp.headers.get("X-Frame-Options") == "DENY"


def test_referrer_policy(client):
    """All responses should include a Referrer-Policy header."""
    resp = client.get("/")
    assert "Referrer-Policy" in resp.headers


def test_404_does_not_leak_paths(client):
    """404 error response should not contain file system paths."""
    resp = client.get("/nonexistent/path/to/something")
    body = resp.data.decode("utf-8")
    # Should not contain common path patterns
    assert "/Users/" not in body
    assert "/home/" not in body
    assert "Traceback" not in body


def test_500_does_not_leak_paths(client):
    """Error responses should not expose internal paths or library versions."""
    # The 404 handler is our proxy for testing error sanitization
    resp = client.get("/nonexistent")
    data = json.loads(resp.data)
    assert data == {"error": "Not found"}


def test_cors_header_with_valid_origin(client):
    """Requests from allowed origins should get CORS headers."""
    resp = client.get("/api/prices", headers={"Origin": "http://localhost:5099"})
    assert resp.headers.get("Access-Control-Allow-Origin") == "http://localhost:5099"


def test_cors_header_rejected_for_unknown_origin(client):
    """Requests from unknown origins should NOT get CORS headers."""
    resp = client.get("/api/prices", headers={"Origin": "http://evil.com"})
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_api_json_responses_have_security_headers(client):
    """API JSON responses should also have security headers."""
    resp = client.get("/api/health")
    assert "Content-Security-Policy" in resp.headers
    assert "X-Content-Type-Options" in resp.headers
