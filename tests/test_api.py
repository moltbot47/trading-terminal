"""Tests for API endpoints -- verify status codes and response shapes."""

import json


def test_index_returns_html(client):
    """GET / should return 200 with HTML content."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert b"Trading Terminal" in resp.data


def test_favicon_returns_204(client):
    """GET /favicon.ico should return 204 No Content."""
    resp = client.get("/favicon.ico")
    assert resp.status_code == 204


def test_api_prices_returns_json(client):
    """GET /api/prices should return 200 with a JSON object."""
    resp = client.get("/api/prices")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)


def test_api_regime_returns_json(client):
    """GET /api/regime should return 200 with a JSON object."""
    resp = client.get("/api/regime")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)


def test_api_news_returns_json_array(client):
    """GET /api/news should return 200 with a JSON array."""
    resp = client.get("/api/news")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_api_positions_returns_json(client):
    """GET /api/positions should return 200 with latpfn and trend_follower keys."""
    resp = client.get("/api/positions")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)
    assert "latpfn" in data
    assert "trend_follower" in data


def test_api_health_returns_json(client):
    """GET /api/health should return 200 with heartbeat, drawdown, system keys."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)
    assert "heartbeat" in data
    assert "drawdown" in data
    assert "system" in data


def test_api_broker_trades_returns_json_array(client):
    """GET /api/broker-trades should return 200 with a JSON array."""
    resp = client.get("/api/broker-trades")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_api_broker_stats_returns_json(client):
    """GET /api/broker-stats should return 200 with a JSON object."""
    resp = client.get("/api/broker-stats")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)


def test_api_predictions_recent_returns_json_array(client):
    """GET /api/predictions-recent should return 200 with a JSON array."""
    resp = client.get("/api/predictions-recent")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_api_polymarket_returns_json_array(client):
    """GET /api/polymarket-forecasts should return 200 with a JSON array."""
    resp = client.get("/api/polymarket-forecasts")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_api_turbo_signals_returns_json_array(client):
    """GET /api/turbo-signals should return 200 with a JSON array."""
    resp = client.get("/api/turbo-signals")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_api_turbo_stats_returns_json(client):
    """GET /api/turbo-stats should return 200 with a JSON object."""
    resp = client.get("/api/turbo-stats")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, dict)


def test_api_candles_valid_symbol(client):
    """GET /api/candles/MNQ should return 200 with a JSON array."""
    resp = client.get("/api/candles/MNQ")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert isinstance(data, list)


def test_healthz_returns_json(client):
    """GET /healthz should return JSON with status field."""
    resp = client.get("/healthz")
    assert resp.status_code in (200, 503)
    data = json.loads(resp.data)
    assert "status" in data
    assert data["status"] in ("healthy", "degraded", "unhealthy")


def test_healthz_has_instruments_field(client):
    """GET /healthz should include instruments detail when data exists."""
    resp = client.get("/healthz")
    data = json.loads(resp.data)
    # If healthy/degraded, instruments field should be present
    if data["status"] != "unhealthy":
        assert "instruments" in data
        assert "freshest_age_seconds" in data


def test_api_candles_invalid_symbol(client):
    """GET /api/candles/INVALID should return 200 with empty array."""
    resp = client.get("/api/candles/INVALID")
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data == []


def test_404_returns_json(client):
    """Non-existent routes should return 404 JSON, not HTML error page."""
    resp = client.get("/nonexistent")
    assert resp.status_code == 404
    data = json.loads(resp.data)
    assert "error" in data
