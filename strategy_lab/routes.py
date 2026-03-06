"""Flask Blueprint for Strategy Lab API endpoints."""

import contextlib
import json
import logging
import threading
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None  # type: ignore[assignment]
from flask import Blueprint, Response, jsonify, request

from strategy_lab import importer, models
from strategy_lab.indicators import volume_profile

logger = logging.getLogger(__name__)

lab_bp = Blueprint("strategy_lab", __name__, url_prefix="/api/lab")

# Background import lock (only one import at a time)
_import_lock = threading.Lock()
_import_status: dict = {"busy": False, "stage": "", "error": ""}


@lab_bp.route("/strategies")
def list_strategies() -> Response:
    """List all strategies (active and inactive)."""
    strategies = models.get_strategies(active_only=False)
    # Parse JSON fields for the frontend
    for s in strategies:
        for field in ("instruments", "entry_rules", "exit_rules", "direction_rules", "indicators_config"):
            if isinstance(s.get(field), str):
                with contextlib.suppress(json.JSONDecodeError, TypeError):
                    s[field] = json.loads(s[field])
    return jsonify(strategies)


@lab_bp.route("/strategies/<int:strategy_id>")
def get_strategy(strategy_id: int) -> Response:
    s = models.get_strategy(strategy_id)
    if not s:
        return jsonify({"error": "Strategy not found"}), 404
    for field in ("instruments", "entry_rules", "exit_rules", "direction_rules", "indicators_config"):
        if isinstance(s.get(field), str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                s[field] = json.loads(s[field])
    return jsonify(s)


@lab_bp.route("/strategies", methods=["POST"])
def create_strategy() -> Response:
    """Create a strategy from JSON body (manual entry)."""
    data = request.get_json()
    if not data or not data.get("name") or not data.get("entry_rules"):
        return jsonify({"error": "name and entry_rules required"}), 400

    sid = models.create_strategy(
        name=data["name"],
        entry_rules=data["entry_rules"],
        exit_rules=data.get("exit_rules", {}),
        direction_rules=data.get("direction_rules"),
        indicators_config=data.get("indicators_config"),
        description=data.get("description", ""),
        source_url=data.get("source_url", ""),
        source_type=data.get("source_type", "manual"),
        transcript=data.get("transcript", ""),
        timeframe=data.get("timeframe", "5m"),
        instruments=data.get("instruments"),
        risk_reward_target=data.get("risk_reward_target", 2.0),
        highlights=data.get("highlights"),
        edge_summary=data.get("edge_summary", ""),
    )
    return jsonify({"id": sid, "status": "created"})


@lab_bp.route("/strategies/<int:strategy_id>/toggle", methods=["POST"])
def toggle_strategy(strategy_id: int) -> Response:
    new_state = models.toggle_strategy(strategy_id)
    return jsonify({"id": strategy_id, "active": new_state})


@lab_bp.route("/strategies/<int:strategy_id>", methods=["DELETE"])
def delete_strategy(strategy_id: int) -> Response:
    models.delete_strategy(strategy_id)
    return jsonify({"status": "deleted"})


@lab_bp.route("/import/youtube", methods=["POST"])
def import_youtube() -> Response:
    """Import strategy from YouTube URL (async-ish with status polling)."""
    global _import_status

    data = request.get_json()
    url = (data or {}).get("url", "").strip()
    if not url:
        return jsonify({"error": "url required"}), 400

    if _import_lock.locked():
        return jsonify({"error": "Import already in progress", "status": _import_status}), 409

    def _do_import():
        global _import_status
        try:
            _import_status = {"busy": True, "stage": "downloading", "error": ""}
            result = importer.import_from_youtube(url)

            if not result or result.get("error"):
                err = result.get("error", "Unknown error") if result else "Unknown error"
                if "transcribe" in err.lower():
                    err = "YouTube blocked this request (cloud IP). Try importing from your local dashboard at http://localhost:5099"
                _import_status = {"busy": False, "stage": "failed", "error": err}
                return

            _import_status = {"busy": True, "stage": "saving", "error": ""}
            sid = models.create_strategy(
                name=result.get("name", "Imported Strategy"),
                entry_rules=result.get("entry_rules", []),
                exit_rules=result.get("exit_rules", {}),
                direction_rules=result.get("direction_rules"),
                indicators_config=result.get("indicators_config"),
                description=result.get("description", ""),
                source_url=url,
                source_type="youtube",
                transcript=result.get("transcript", ""),
                timeframe=result.get("timeframe", "5m"),
                instruments=result.get("instruments"),
                risk_reward_target=result.get("risk_reward_target", 2.0),
                highlights=result.get("highlights"),
                edge_summary=result.get("edge_summary", ""),
                transcript_segments=result.get("transcript_segments"),
                video_duration=result.get("video_duration", 0),
            )
            _import_status = {"busy": False, "stage": "complete", "error": "", "strategy_id": sid}
        except Exception as e:
            _import_status = {"busy": False, "stage": "failed", "error": str(e)}
        finally:
            _import_lock.release()

    _import_lock.acquire()
    thread = threading.Thread(target=_do_import, daemon=True, name="youtube-import")
    thread.start()

    return jsonify({"status": "started", "message": "Import started in background"})


@lab_bp.route("/import/transcript", methods=["POST"])
def import_transcript() -> Response:
    """Import strategy from raw transcript text."""
    data = request.get_json()
    transcript = (data or {}).get("transcript", "").strip()
    source_url = (data or {}).get("source_url", "")

    if not transcript:
        return jsonify({"error": "transcript required"}), 400

    result = importer.import_from_transcript(transcript, source_url)
    if not result or result.get("error"):
        return jsonify({"error": result.get("error", "Failed to extract strategy")}), 422

    sid = models.create_strategy(
        name=result.get("name", "Imported Strategy"),
        entry_rules=result.get("entry_rules", []),
        exit_rules=result.get("exit_rules", {}),
        direction_rules=result.get("direction_rules"),
        indicators_config=result.get("indicators_config"),
        description=result.get("description", ""),
        source_url=source_url,
        source_type="transcript",
        transcript=transcript[:10000],
        timeframe=result.get("timeframe", "5m"),
        instruments=result.get("instruments"),
        risk_reward_target=result.get("risk_reward_target", 2.0),
        highlights=result.get("highlights"),
        edge_summary=result.get("edge_summary", ""),
    )
    return jsonify({"id": sid, "status": "created", "strategy": result})


@lab_bp.route("/import/status")
def import_status() -> Response:
    return jsonify(_import_status)


@lab_bp.route("/scanner/hits")
def scanner_hits() -> Response:
    """Recent scanner hits across all strategies."""
    limit = request.args.get("limit", 50, type=int)
    hits = models.get_recent_hits(limit=min(limit, 200))
    for h in hits:
        if isinstance(h.get("conditions_met"), str):
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                h["conditions_met"] = json.loads(h["conditions_met"])
    return jsonify(hits)


@lab_bp.route("/scanner/active")
def scanner_active() -> Response:
    """Currently active (simulating) trades."""
    hits = models.get_active_hits()
    return jsonify(hits)


@lab_bp.route("/stats")
def lab_stats() -> Response:
    """Aggregate stats across all strategies."""
    return jsonify(models.get_strategy_stats())


@lab_bp.route("/stats/<int:strategy_id>")
def strategy_stats(strategy_id: int) -> Response:
    return jsonify(models.get_strategy_stats(strategy_id))


@lab_bp.route("/analytics")
def analytics() -> Response:
    """Extended analytics: per-strategy breakdown."""
    strategies = models.get_strategies(active_only=False)
    result = []
    for s in strategies:
        stats = models.get_strategy_stats(s["id"])
        stats["strategy_id"] = s["id"]
        stats["name"] = s["name"]
        stats["active"] = bool(s["active"])
        stats["timeframe"] = s["timeframe"]
        stats["total_scans"] = s["total_scans"]
        result.append(stats)
    return jsonify(result)


# ---------------------------------------------------------------------------
# VPE (Volume Profile Edges) confirmation endpoint
# ---------------------------------------------------------------------------

_YF_MAP = {
    "MNQ": "NQ=F", "MYM": "YM=F", "MGC": "GC=F", "MBT": "BTC=F",
    "ES": "ES=F", "NQ": "NQ=F", "GC": "GC=F", "YM": "YM=F",
}

# Simple TTL cache: {symbol: (timestamp, payload)}
_vpe_cache: dict[str, tuple[float, dict]] = {}
_VPE_CACHE_TTL = 60  # seconds


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten MultiIndex columns that yfinance may return."""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if isinstance(col, tuple) else col for col in df.columns]
    return df


def _compute_key_levels(df: pd.DataFrame) -> dict:
    """Compute PDH, PDL, ONH, ONL from 5-min bars.

    Prior Day = yesterday's RTH session (09:30-16:00 ET).
    Overnight  = 18:00 ET yesterday to 09:30 ET today.
    """
    et = ZoneInfo("US/Eastern")
    now_et = datetime.now(et)
    today = now_et.date()
    yesterday = today - timedelta(days=1)
    # Skip weekends for yesterday
    while yesterday.weekday() >= 5:
        yesterday -= timedelta(days=1)

    result = {"pdh": None, "pdl": None, "onh": None, "onl": None}

    idx = df.index
    if idx.tz is None:
        with contextlib.suppress(Exception):
            idx = idx.tz_localize("UTC")
    with contextlib.suppress(Exception):
        idx = idx.tz_convert(et)

    df_et = df.copy()
    df_et.index = idx

    # Prior day RTH: yesterday 09:30 - 16:00 ET
    rth_start = datetime.combine(yesterday, datetime.min.time().replace(hour=9, minute=30), tzinfo=et)
    rth_end = datetime.combine(yesterday, datetime.min.time().replace(hour=16), tzinfo=et)
    rth = df_et[(df_et.index >= rth_start) & (df_et.index < rth_end)]
    if not rth.empty:
        result["pdh"] = float(rth["High"].max())
        result["pdl"] = float(rth["Low"].min())

    # Overnight: 18:00 ET yesterday to 09:30 ET today
    on_start = datetime.combine(yesterday, datetime.min.time().replace(hour=18), tzinfo=et)
    on_end = datetime.combine(today, datetime.min.time().replace(hour=9, minute=30), tzinfo=et)
    on = df_et[(df_et.index >= on_start) & (df_et.index < on_end)]
    if not on.empty:
        result["onh"] = float(on["High"].max())
        result["onl"] = float(on["Low"].min())

    return result


def _detect_signal_candle(df: pd.DataFrame) -> dict:
    """Detect doji, shooting star, or hammer on the last closed candle."""
    result = {"detected": False, "type": None, "volume_ratio": 0.0,
              "direction": None, "at_edge": False, "edge_name": None}

    if len(df) < 3:
        return result

    candle = df.iloc[-2]  # last closed candle
    prior = df.iloc[-3]

    o, h, lo, c = float(candle["Open"]), float(candle["High"]), float(candle["Low"]), float(candle["Close"])
    body = abs(c - o)
    rng = h - lo
    if rng < 1e-10:
        return result

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - lo
    vol = float(candle.get("Volume", 0))
    prior_vol = float(prior.get("Volume", 1))
    vol_ratio = vol / prior_vol if prior_vol > 0 else 0.0

    candle_type = None
    direction = None

    # Doji
    if body < 0.3 * rng:
        candle_type = "doji"
        direction = "neutral"
    # Shooting star (bearish)
    elif upper_wick > 2 * body and lower_wick < 0.3 * body:
        candle_type = "shooting_star"
        direction = "bearish"
    # Hammer (bullish)
    elif lower_wick > 2 * body and upper_wick < 0.3 * body:
        candle_type = "hammer"
        direction = "bullish"

    if candle_type and vol > prior_vol:
        result["detected"] = True
        result["type"] = candle_type
        result["volume_ratio"] = round(vol_ratio, 2)
        result["direction"] = direction

    return result


def _compute_vp_shape(df: pd.DataFrame, lookback: int = 50) -> str:
    """Determine VP shape: P (bullish), B (bearish), D (neutral).

    P-shape = volume accumulated at top of range.
    B-shape = volume accumulated at bottom of range.
    D-shape = balanced in the middle.
    """
    window = df.tail(lookback)
    if len(window) < 10:
        return "D"

    mid_price = (float(window["High"].max()) + float(window["Low"].min())) / 2
    upper = window[window["Close"] >= mid_price]
    lower = window[window["Close"] < mid_price]

    upper_vol = float(upper["Volume"].sum()) if not upper.empty else 0
    lower_vol = float(lower["Volume"].sum()) if not lower.empty else 0

    if upper_vol > 1.3 * lower_vol:
        return "P"
    elif lower_vol > 1.3 * upper_vol:
        return "B"
    return "D"


def _check_confluence(key_levels: dict, vp: dict, threshold_pct: float = 0.3) -> list[dict]:
    """Check if any key level is within threshold_pct% of VAH or VAL."""
    edges = {"VAH": vp.get("vah"), "VAL": vp.get("val")}
    confluences = []

    for level_name, level_price in key_levels.items():
        if level_price is None:
            continue
        for edge_name, edge_price in edges.items():
            if edge_price is None or edge_price == 0:
                continue
            dist_pct = abs(level_price - edge_price) / edge_price * 100
            if dist_pct <= threshold_pct:
                confluences.append({
                    "level": level_name.upper(),
                    "price": round(level_price, 2),
                    "near_edge": edge_name,
                    "edge_price": round(edge_price, 2),
                    "distance_pct": round(dist_pct, 2),
                })

    return confluences


def _compute_bias(vp_shape: str, price: float, poc: float | None) -> str:
    """Derive directional bias from VP shape and price vs POC."""
    if poc is None:
        return "neutral"

    shape_bias = {"P": "bullish", "B": "bearish", "D": "neutral"}.get(vp_shape, "neutral")
    price_bias = "bullish" if price > poc else ("bearish" if price < poc else "neutral")

    if shape_bias == price_bias:
        return shape_bias
    if shape_bias == "neutral":
        return price_bias
    if price_bias == "neutral":
        return shape_bias
    return "neutral"  # conflicting signals


@lab_bp.route("/vpe/<symbol>")
def vpe_dashboard(symbol: str) -> Response:
    """Compute and return VPE (Volume Profile Edges) confirmation data."""
    import time

    symbol = symbol.upper()

    if yf is None:
        return jsonify({"error": "yfinance not available"}), 503

    # Check cache
    cached = _vpe_cache.get(symbol)
    if cached and (time.time() - cached[0]) < _VPE_CACHE_TTL:
        return jsonify(cached[1])

    # Fetch bars via yfinance Ticker.history() (avoids session cache bugs)
    yf_sym = _YF_MAP.get(symbol, symbol)
    try:
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(period="5d", interval="5m")
    except Exception as e:
        logger.warning("VPE: yfinance fetch failed for %s: %s", symbol, e)
        return jsonify({"error": f"Failed to fetch data for {symbol}", "detail": str(e)}), 502

    if df is None or df.empty:
        return jsonify({"error": f"No data returned for {symbol}. Market may be closed."}), 404

    df = _flatten_columns(df)

    # Ensure required columns exist
    for col in ("Open", "High", "Low", "Close", "Volume"):
        if col not in df.columns:
            return jsonify({"error": f"Missing column {col} in data"}), 500

    df = df.dropna(subset=["Close"])
    if len(df) < 10:
        return jsonify({"error": "Insufficient bar data", "bars": len(df)}), 404

    current_price = float(df["Close"].iloc[-1])

    # --- Volume Profile ---
    vp_data = volume_profile(df, lookback=min(50, len(df) - 1), num_bins=20)
    # Get latest non-NaN values
    poc_val = vah_val = val_val = None
    with contextlib.suppress(Exception):
        poc_series = vp_data["poc"].dropna()
        vah_series = vp_data["vah"].dropna()
        val_series = vp_data["val"].dropna()
        if not poc_series.empty:
            poc_val = round(float(poc_series.iloc[-1]), 2)
        if not vah_series.empty:
            vah_val = round(float(vah_series.iloc[-1]), 2)
        if not val_series.empty:
            val_val = round(float(val_series.iloc[-1]), 2)

    vp_dict = {"poc": poc_val, "vah": vah_val, "val": val_val}

    # --- Key Levels ---
    key_levels = _compute_key_levels(df)
    # Round non-None values
    key_levels = {k: round(v, 2) if v is not None else None for k, v in key_levels.items()}

    # --- Signal Candle ---
    signal = _detect_signal_candle(df)

    # Check if signal candle is at a VP edge (within 0.3% of VAH or VAL)
    if signal["detected"]:
        candle_close = float(df["Close"].iloc[-2])
        for edge_name, edge_val in [("VAH", vah_val), ("VAL", val_val)]:
            if edge_val and edge_val > 0 and abs(candle_close - edge_val) / edge_val * 100 <= 0.3:
                    signal["at_edge"] = True
                    signal["edge_name"] = edge_name
                    break

    # --- VP Shape ---
    vp_shape = _compute_vp_shape(df, lookback=min(50, len(df) - 1))

    # --- Bias ---
    bias = _compute_bias(vp_shape, current_price, poc_val)

    # --- Confluence ---
    confluence = _check_confluence(key_levels, vp_dict)

    # --- Confirmations ---
    at_vp_edge = False
    if vah_val and val_val:
        for edge_val in (vah_val, val_val):
            if edge_val > 0 and abs(current_price - edge_val) / edge_val * 100 <= 0.5:
                at_vp_edge = True
                break

    signal_valid = signal["detected"]
    confluence_found = len(confluence) > 0

    # Bias aligned = signal direction matches overall bias (or signal is neutral/doji)
    bias_aligned = False
    if signal_valid:
        if signal["direction"] == bias:
            bias_aligned = True
        elif signal["direction"] == "neutral":
            bias_aligned = True  # doji at edge is valid for either direction
    elif bias != "neutral":
        bias_aligned = True  # no signal candle but bias exists

    score = sum([at_vp_edge, signal_valid, confluence_found, bias_aligned])
    grade_map = {4: "A", 3: "B", 2: "C"}
    setup_grade = grade_map.get(score, "D")

    # Price vs POC description
    price_vs_poc = "N/A"
    if poc_val:
        diff = current_price - poc_val
        pct = abs(diff / poc_val * 100)
        side = "above" if diff > 0 else "below"
        price_vs_poc = f"{pct:.1f}% {side} POC ({poc_val:.2f})"

    payload = {
        "symbol": symbol,
        "price": round(current_price, 2),
        "volume_profile": vp_dict,
        "key_levels": key_levels,
        "signal_candle": signal,
        "vp_shape": vp_shape,
        "bias": bias,
        "price_vs_poc": price_vs_poc,
        "confluence": confluence,
        "confirmations": {
            "at_vp_edge": at_vp_edge,
            "signal_candle_valid": signal_valid,
            "confluence_found": confluence_found,
            "bias_aligned": bias_aligned,
            "setup_grade": setup_grade,
        },
    }

    # Cache result
    _vpe_cache[symbol] = (time.time(), payload)

    return jsonify(payload)
