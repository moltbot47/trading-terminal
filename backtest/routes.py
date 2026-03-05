"""Flask Blueprint for backtester API at /api/backtest/."""

import logging

from flask import Blueprint, jsonify, request

from backtest import models
from backtest.data import AlpacaDataFetcher
from backtest.engine import BacktestEngine
from strategy_lab.models import get_strategy

logger = logging.getLogger(__name__)

bt_bp = Blueprint("backtest", __name__, url_prefix="/api/backtest")

# Supported symbols for the equity backtester
SYMBOLS = ["QQQ", "TQQQ", "SPY", "SPXL", "NVDA", "TSLA", "AMD"]


@bt_bp.route("/run", methods=["POST"])
def run_backtest():
    """Launch a backtest run.

    Body: {strategy_id, symbol, start_date, end_date}
    """
    body = request.get_json(silent=True) or {}

    strategy_id = body.get("strategy_id")
    symbol = body.get("symbol", "QQQ")
    start_date = body.get("start_date")
    end_date = body.get("end_date")

    if not strategy_id:
        return jsonify({"error": "strategy_id is required"}), 400
    if not start_date or not end_date:
        return jsonify({"error": "start_date and end_date are required"}), 400

    # Load strategy
    strategy = get_strategy(int(strategy_id))
    if not strategy:
        return jsonify({"error": f"Strategy {strategy_id} not found"}), 404

    # Fetch bars
    try:
        fetcher = AlpacaDataFetcher()
        bars_df = fetcher.get_bars(symbol, start_date, end_date)
    except Exception as e:
        logger.error("Data fetch error: %s", e)
        return jsonify({"error": f"Failed to fetch data: {e}"}), 500

    if bars_df is None or bars_df.empty:
        return jsonify({"error": f"No data available for {symbol} in date range"}), 404

    # Create run record
    run_id = models.create_run(
        strategy_id=int(strategy_id),
        symbol=symbol,
        timeframe=body.get("timeframe", "5Min"),
        start_date=start_date,
        end_date=end_date,
    )

    # Run backtest
    try:
        engine = BacktestEngine(strategy, bars_df)
        results = engine.run(run_id)
    except Exception as e:
        logger.error("Backtest engine error (run %d): %s", run_id, e)
        models.update_run(run_id, status="failed")
        return jsonify({"error": f"Backtest failed: {e}"}), 500

    return jsonify(results), 200


@bt_bp.route("/runs", methods=["GET"])
def list_runs():
    """List recent backtest runs."""
    limit = request.args.get("limit", 30, type=int)
    runs = models.get_runs(limit=limit)
    return jsonify(runs), 200


@bt_bp.route("/runs/<int:run_id>", methods=["GET"])
def get_run(run_id):
    """Get a single run with its trades."""
    run = models.get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    trades = models.get_trades(run_id)
    run["trades"] = trades
    return jsonify(run), 200


@bt_bp.route("/runs/<int:run_id>", methods=["DELETE"])
def delete_run(run_id):
    """Delete a run and its trades."""
    run = models.get_run(run_id)
    if not run:
        return jsonify({"error": "Run not found"}), 404
    models.delete_run(run_id)
    return jsonify({"ok": True}), 200


@bt_bp.route("/symbols", methods=["GET"])
def list_symbols():
    """Return supported symbols."""
    return jsonify(SYMBOLS), 200
