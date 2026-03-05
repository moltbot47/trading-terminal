#!/usr/bin/env python3
"""Trading Terminal v3 -- Flask routes & API endpoints.

Live system dashboard with prices, ADX regime, news, positions,
broker stats, predictions, turbo signals, and Polymarket forecasts.
"""

import json
import logging
import math
import os
import sqlite3
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

# Ensure our local directory is first in sys.path so our config.py wins
# over ~/latpfn-trading/config/ package
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

# Add latpfn-trading to path for its imports (after our directory)
_PROJ = os.path.expanduser("~/latpfn-trading")
if _PROJ not in sys.path:
    sys.path.append(_PROJ)

import pandas as pd
from flask import Flask, Response, jsonify, render_template, request
from signals.news_filter import NewsFilter
from signals.regime import detect_regime
from strategies.trend_follower.price_feed import PriceFeed, PriceSnapshot

import config as _cfg


class _JSONFormatter(logging.Formatter):
    """Structured JSON log formatter for production use."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry)


_handler = logging.StreamHandler()
_handler.setFormatter(_JSONFormatter())
logging.basicConfig(level=logging.WARNING, handlers=[_handler])
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Security: CSP + CORS headers
# ---------------------------------------------------------------------------


@app.after_request
def add_security_headers(response: Response) -> Response:
    """Add security headers to every response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    # CORS -- localhost only
    origin = request.headers.get("Origin", "")
    if origin in _cfg.CORS_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


# ---------------------------------------------------------------------------
# Rate limiting (simple in-memory per-IP counter)
# ---------------------------------------------------------------------------
_rate_lock = threading.Lock()
_rate_counters: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit() -> bool:
    """Return True if request is within rate limit, False otherwise."""
    ip = request.remote_addr or "unknown"
    now = time.time()
    cutoff = now - _cfg.RATE_LIMIT_WINDOW
    with _rate_lock:
        timestamps = _rate_counters[ip]
        # Prune old entries
        _rate_counters[ip] = [t for t in timestamps if t > cutoff]
        if len(_rate_counters[ip]) >= _cfg.RATE_LIMIT_MAX_REQUESTS:
            return False
        _rate_counters[ip].append(now)
    return True


@app.before_request
def rate_limit_check() -> Response | None:
    """Reject requests exceeding rate limit."""
    if not _check_rate_limit():
        return Response(
            json.dumps({"error": "Rate limit exceeded"}),
            status=429,
            content_type="application/json",
        )
    return None


# ---------------------------------------------------------------------------
# Error handlers -- don't leak internal paths
# ---------------------------------------------------------------------------


@app.errorhandler(404)
def not_found(e: Exception) -> tuple[Response, int]:
    """Handle 404 errors without exposing internals."""
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def internal_error(e: Exception) -> tuple[Response, int]:
    """Handle 500 errors without exposing internals."""
    logger.error("Internal server error: %s", e)
    return jsonify({"error": "Internal server error"}), 500


# ---------------------------------------------------------------------------
# Monkey-patch PriceFeed for safe yfinance fetches
# ---------------------------------------------------------------------------


def _parse_snapshot_df(df: pd.DataFrame, inst: str, now: float) -> PriceSnapshot | None:
    """Parse a single-ticker yfinance DataFrame into a PriceSnapshot."""
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    last = df.iloc[-1]
    price, high, low = float(last["Close"]), float(last["High"]), float(last["Low"])
    if math.isnan(price):
        return None
    vol = last.get("Volume", 0)
    vol = 0 if (vol is None or (isinstance(vol, float) and math.isnan(vol))) else int(vol)
    return PriceSnapshot(symbol=inst, price=price, high=high, low=low, volume=vol, timestamp=now)


def _safe_snapshot(self: PriceFeed) -> dict[str, PriceSnapshot]:
    """Fetch each ticker individually with caching to avoid multi-ticker bugs."""
    import yfinance as yf

    now = time.time()
    if now - self._snapshot_cache_time < _cfg.SNAPSHOT_CACHE_TTL:
        return dict(self._last_snapshots)
    try:
        for yf_tick, inst in self._ticker_to_inst.items():
            try:
                df = yf.download(yf_tick, period="1d", interval="1m", progress=False)
                snap = _parse_snapshot_df(df, inst, now)
                if snap:
                    self._last_snapshots[inst] = snap
            except Exception as exc:
                logger.debug("Snapshot fetch failed for %s: %s", yf_tick, exc)
        self._snapshot_cache_time = now
        return dict(self._last_snapshots)
    except Exception as e:
        logger.warning("Snapshot fetch failed: %s", e)
        return dict(self._last_snapshots)


def _safe_full_bars(self: PriceFeed, days: int = 5, interval: str = "5m") -> dict[str, pd.DataFrame | None]:
    """Fetch full OHLCV bars for each ticker with error handling (BUG-010 fix)."""
    import yfinance as yf

    result: dict[str, pd.DataFrame | None] = {}
    for yf_tick, inst in self._ticker_to_inst.items():
        try:
            df = yf.download(yf_tick, period=f"{days}d", interval=interval, progress=False)
            if df is not None and not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                result[inst] = df
            else:
                result[inst] = None
        except Exception as e:
            logger.warning("full_bars fetch error for %s: %s", yf_tick, e)
            result[inst] = None
    return result


PriceFeed.snapshot = _safe_snapshot
PriceFeed.full_bars = _safe_full_bars

# ---------------------------------------------------------------------------
# Shared state with thread locks (BUG-009 fix)
# ---------------------------------------------------------------------------
price_feed = PriceFeed(_cfg.INSTRUMENTS)

# Override MBT mapping: PriceFeed uses BTC-USD (spot) but we want BTC=F (futures)
if "BTC-USD" in price_feed._ticker_to_inst:
    del price_feed._ticker_to_inst["BTC-USD"]
    price_feed._ticker_to_inst["BTC=F"] = "MBT"

news_filter = NewsFilter(_cfg.NEWS_FILTER_CONFIG)

_cache_lock = threading.Lock()
_regime_cache: dict[str, Any] = {}
_regime_cache_time: float = 0.0
_bars_cache: dict[str, pd.DataFrame | None] = {}
_bars_cache_time: float = 0.0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_html(text: str) -> str:
    """Escape HTML special characters to prevent XSS."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def query(db_file: str, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Execute a read-only query against a SQLite database in the data directory."""
    path = os.path.join(_cfg.DATA, db_file)
    if not os.path.exists(path):
        return []
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def read_json(filename: str) -> Any:
    """Read and parse a JSON file from the data directory."""
    path = os.path.join(_cfg.DATA, filename)
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _sanitize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sanitize string values in database rows to prevent XSS."""
    sanitized = []
    for row in rows:
        clean = {}
        for k, v in row.items():
            if isinstance(v, str):
                clean[k] = _escape_html(v)
            else:
                clean[k] = v
        sanitized.append(clean)
    return sanitized


# ---------------------------------------------------------------------------
# Favicon (BUG-017 fix)
# ---------------------------------------------------------------------------


@app.route("/favicon.ico")
def favicon() -> tuple[str, int]:
    """Return 204 No Content for favicon requests."""
    return "", 204


# ---------------------------------------------------------------------------
# Health Endpoint
# ---------------------------------------------------------------------------


@app.route("/healthz")
def healthz() -> tuple[Response, int]:
    """Return 200 if price data is fresh, 503 if stale.

    Checks that at least one instrument has been updated within
    the last 60 seconds. Used by uptime monitors and CI smoke tests.
    """
    snapshots = price_feed._last_snapshots
    now = time.time()
    max_age = 120.0  # seconds — allow 2 minutes for yfinance lag

    if not snapshots:
        return jsonify({"status": "unhealthy", "reason": "no price data"}), 503

    freshest = max(s.timestamp for s in snapshots.values())
    age = now - freshest
    instruments_status = {}
    for inst, snap in snapshots.items():
        inst_age = now - snap.timestamp
        instruments_status[inst] = {
            "price": snap.price,
            "age_seconds": round(inst_age, 1),
            "stale": inst_age > max_age,
        }

    healthy = age < max_age
    return jsonify({
        "status": "healthy" if healthy else "degraded",
        "freshest_age_seconds": round(age, 1),
        "instruments": instruments_status,
    }), 200 if healthy else 503


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


@app.route("/")
def index() -> str:
    """Serve the main trading terminal dashboard."""
    return render_template("dashboard.html")


# ---------------------------------------------------------------------------
# API: Candlestick Data for Lightweight Charts (BUG-006 fix: batch download)
# ---------------------------------------------------------------------------

_candles_lock = threading.Lock()
_candles_cache: dict[str, list[dict]] = {}
_candles_cache_time: float = 0.0
_CANDLES_CACHE_TTL: float = 30.0  # cache candles for 30s


def _df_to_candles(sym_df: pd.DataFrame) -> list[dict]:
    """Convert a pandas OHLC DataFrame to TradingView candle format."""
    if isinstance(sym_df.columns, pd.MultiIndex):
        sym_df.columns = sym_df.columns.get_level_values(-1)
    candles = []
    for idx, row in sym_df.iterrows():
        o, h, lo, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
        if not (math.isnan(o) or math.isnan(h) or math.isnan(lo) or math.isnan(c)):
            candles.append({"time": int(idx.timestamp()), "open": round(o, 2),
                            "high": round(h, 2), "low": round(lo, 2), "close": round(c, 2)})
    return candles


def _parse_batch_candles(df: pd.DataFrame, tickers: list[str]) -> dict[str, list[dict]]:
    """Parse batch yfinance download into per-symbol candle lists."""
    result: dict[str, list[dict]] = {}
    for sym, yf_sym in _cfg.YF_MAP.items():
        try:
            if len(tickers) == 1:
                sym_df = df
            else:
                sym_df = df[yf_sym] if yf_sym in df.columns.get_level_values(0) else None
            if sym_df is None or sym_df.empty:
                result[sym] = []
                continue
            result[sym] = _df_to_candles(sym_df)
        except Exception as e:
            logger.warning("Candle parse error for %s: %s", sym, e)
            result[sym] = []
    return result


@app.route("/api/candles/<symbol>")
def api_candles(symbol: str) -> Response:
    """Return OHLC data in TradingView Lightweight Charts format.

    Uses batch yf.download for all symbols simultaneously to reduce
    API calls (BUG-006 fix). Results are cached for 30 seconds.
    """
    import yfinance as yf

    symbol = symbol.upper()
    if symbol not in _cfg.YF_MAP:
        return jsonify([])

    global _candles_cache, _candles_cache_time
    now = time.time()

    with _candles_lock:
        if now - _candles_cache_time < _CANDLES_CACHE_TTL and symbol in _candles_cache:
            return jsonify(_candles_cache[symbol])

    # Batch download all symbols at once (BUG-006 fix)
    tickers = list(_cfg.YF_MAP.values())
    try:
        df = yf.download(tickers, period="5d", interval="5m", progress=False, group_by="ticker")
    except Exception as e:
        logger.warning("Batch candles fetch error: %s", e)
        return jsonify([])

    new_cache = _parse_batch_candles(df, tickers)

    with _candles_lock:
        _candles_cache = new_cache
        _candles_cache_time = time.time()

    return jsonify(_candles_cache.get(symbol, []))


# ---------------------------------------------------------------------------
# API: Live Prices (5-sec polling)
# ---------------------------------------------------------------------------


@app.route("/api/prices")
def api_prices() -> Response:
    """Return current live prices for all tracked instruments."""
    snapshots = price_feed.snapshot()
    result = {}
    for inst, snap in snapshots.items():
        result[inst] = {
            "price": round(snap.price, 2),
            "high": round(snap.high, 2),
            "low": round(snap.low, 2),
            "volume": snap.volume,
            "timestamp": snap.timestamp,
        }
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: ADX Regime Detection (cached 60s)
# ---------------------------------------------------------------------------


@app.route("/api/regime")
def api_regime() -> Response:
    """Return ADX-based market regime for each instrument.

    Bars data is cached for 5 minutes, regime results for 60 seconds.
    Thread-safe via _cache_lock (BUG-009 fix).
    """
    global _regime_cache, _regime_cache_time, _bars_cache, _bars_cache_time

    now = time.time()

    with _cache_lock:
        bars_stale = now - _bars_cache_time > _cfg.BARS_CACHE_TTL
        regime_stale = now - _regime_cache_time > _cfg.REGIME_CACHE_TTL

    # Refresh bars every 5 min
    if bars_stale:
        try:
            new_bars = price_feed.full_bars(days=5, interval="5m")
            with _cache_lock:
                _bars_cache = new_bars
                _bars_cache_time = time.time()
        except Exception as e:
            logger.warning("full_bars fetch error: %s", e)
            return jsonify({"error": "Failed to fetch bars"})

    # Refresh regime every 60s
    if regime_stale:
        with _cache_lock:
            local_bars = dict(_bars_cache)

        regimes: dict[str, Any] = {}
        for inst, df in local_bars.items():
            if df is not None and len(df) >= 60:
                try:
                    regime = detect_regime(df, instrument=inst)
                    regimes[inst] = regime
                except Exception as e:
                    regimes[inst] = {"regime": "error", "adx": 0, "error": str(e)}

        with _cache_lock:
            _regime_cache = regimes
            _regime_cache_time = time.time()

    with _cache_lock:
        return jsonify(dict(_regime_cache))


# ---------------------------------------------------------------------------
# API: News Calendar (BUG-014 fix: naive datetime guard)
# ---------------------------------------------------------------------------


@app.route("/api/news")
def api_news() -> Response:
    """Return upcoming economic calendar events from Forex Factory.

    Guards against naive datetime objects that would crash
    astimezone() (BUG-014 fix).
    """
    events = news_filter.fetch_calendar()
    now = datetime.now(timezone.utc)
    result = []
    for ev in events:
        ev_date = ev["date"]
        # BUG-014 fix: guard against naive datetimes
        if ev_date.tzinfo is None:
            ev_date = ev_date.replace(tzinfo=timezone.utc)
        ev_time = ev_date.astimezone(timezone.utc)
        mins_away = (ev_time - now).total_seconds() / 60.0
        result.append({
            "title": _escape_html(str(ev.get("title", ""))),
            "impact": _escape_html(str(ev.get("impact", ""))),
            "date": ev_date.isoformat(),
            "minutes_away": round(mins_away, 1),
            "forecast": _escape_html(str(ev.get("forecast", "") or "")),
            "previous": _escape_html(str(ev.get("previous", "") or "")),
        })
    result.sort(key=lambda x: abs(x["minutes_away"]))
    return jsonify(result)


# ---------------------------------------------------------------------------
# API: Open Positions
# ---------------------------------------------------------------------------


@app.route("/api/positions")
def api_positions() -> Response:
    """Return open positions for LaT-PFN and trend follower strategies."""
    positions = read_json("positions.json") or []
    trend_positions = read_json("trend_positions.json") or []
    return jsonify({"latpfn": positions, "trend_follower": trend_positions})


# ---------------------------------------------------------------------------
# API: System Health
# ---------------------------------------------------------------------------


@app.route("/api/health")
def api_health() -> Response:
    """Return system health including heartbeat, drawdown, and state."""
    heartbeat = read_json("heartbeat.json") or {}
    drawdown = read_json("drawdown_state.json") or {}
    system = read_json("system_state.json") or {}
    return jsonify({
        "heartbeat": heartbeat,
        "drawdown": drawdown,
        "system": system,
    })


# ---------------------------------------------------------------------------
# API: Broker Stats & Trades
# ---------------------------------------------------------------------------


@app.route("/api/broker-trades")
def broker_trades() -> Response:
    """Return the 30 most recent broker trades."""
    rows = query("broker_reports.db", """
        SELECT timestamp, instrument, direction, quantity, entry_price, exit_price, pnl, raw_symbol
        FROM broker_trades ORDER BY id DESC LIMIT 30
    """)
    return jsonify(_sanitize_rows(rows))


def _compute_broker_stats(rows: list[dict]) -> dict:
    """Compute aggregate statistics from broker trade rows."""
    pnls = [(r["pnl"] or 0) for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses)) or 1
    return {
        "total_trades": len(rows),
        "total_pnl": round(sum(pnls), 2),
        "win_rate": round(len(wins) / len(rows) * 100, 1),
        "profit_factor": round(gross_win / gross_loss, 2),
        "wins": len(wins),
        "losses": len(losses),
        "avg_win": round(gross_win / len(wins), 2) if wins else 0,
        "avg_loss": round(gross_loss / len(losses), 2) if losses else 0,
        "best_trade": round(max(pnls), 2),
        "worst_trade": round(min(pnls), 2),
    }


@app.route("/api/broker-stats")
def broker_stats() -> Response:
    """Return aggregate broker performance statistics."""
    rows = query("broker_reports.db", "SELECT * FROM broker_trades")
    if not rows:
        return jsonify({})
    return jsonify(_compute_broker_stats(rows))


# ---------------------------------------------------------------------------
# API: Predictions
# ---------------------------------------------------------------------------


@app.route("/api/predictions-recent")
def predictions_recent() -> Response:
    """Return the 30 most recent LaT-PFN prediction records."""
    rows = query("trade_log.db", """
        SELECT timestamp, instrument, direction, composite_confidence, regime,
               shot_tier, current_price, forecast_end_price, signal_generated
        FROM predictions ORDER BY id DESC LIMIT 30
    """)
    return jsonify(_sanitize_rows(rows))


# ---------------------------------------------------------------------------
# API: Polymarket Forecasts
# ---------------------------------------------------------------------------


@app.route("/api/polymarket-forecasts")
def polymarket_forecasts() -> Response:
    """Return the 20 most recent Polymarket LLM forecast comparisons."""
    rows = query("polymarket_forecasts.db", """
        SELECT question, llm_probability, llm_confidence, market_price, model, timestamp, outcome
        FROM forecasts ORDER BY id DESC LIMIT 20
    """)
    return jsonify(_sanitize_rows(rows))


# ---------------------------------------------------------------------------
# API: Turbo Strategy
# ---------------------------------------------------------------------------


@app.route("/api/turbo-signals")
def turbo_signals() -> Response:
    """Return the 40 most recent turbo strategy signals."""
    rows = query("turbo_analytics.db", """
        SELECT timestamp, asset, timeframe, momentum_strength, momentum_direction,
               signal_generated, signal_direction, signal_reason, skip_reason,
               traded, pnl, crypto_price, pct_change_1m, pct_change_3m
        FROM turbo_signals ORDER BY id DESC LIMIT 40
    """)
    return jsonify(_sanitize_rows(rows))


@app.route("/api/turbo-stats")
def turbo_stats() -> Response:
    """Return aggregate turbo strategy statistics."""
    traded = query("turbo_analytics.db", "SELECT COUNT(*) as c FROM turbo_signals WHERE traded=1")
    if not traded:
        return jsonify({})
    traded_count = traded[0]["c"]
    total = query("turbo_analytics.db", "SELECT COUNT(*) as c FROM turbo_signals")[0]["c"]
    wins = query("turbo_analytics.db", "SELECT COUNT(*) as c FROM turbo_signals WHERE traded=1 AND pnl > 0")[0]["c"]
    losses = query("turbo_analytics.db", "SELECT COUNT(*) as c FROM turbo_signals WHERE traded=1 AND pnl < 0")[0]["c"]
    total_pnl = query("turbo_analytics.db", "SELECT COALESCE(SUM(pnl),0) as s FROM turbo_signals WHERE traded=1")[0]["s"]
    assets = query("turbo_analytics.db", """
        SELECT asset, COUNT(*) as signals,
               SUM(CASE WHEN traded=1 THEN 1 ELSE 0 END) as trades,
               COALESCE(SUM(CASE WHEN traded=1 THEN pnl ELSE 0 END),0) as pnl
        FROM turbo_signals GROUP BY asset ORDER BY signals DESC
    """)
    return jsonify({
        "total_signals": total,
        "traded": traded_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / traded_count * 100, 1) if traded_count > 0 else 0,
        "total_pnl": round(total_pnl, 2),
        "assets": _sanitize_rows(assets),
    })


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  TRADING TERMINAL v3.0 -- Live System Dashboard")
    print(f"  http://{_cfg.HOST}:{_cfg.PORT}")
    print("=" * 60)
    app.run(host=_cfg.HOST, port=_cfg.PORT, debug=_cfg.DEBUG)
