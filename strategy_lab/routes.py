"""Flask Blueprint for Strategy Lab API endpoints."""

import contextlib
import json
import logging
import threading

from flask import Blueprint, Response, jsonify, request

from strategy_lab import importer, models

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
                _import_status = {"busy": False, "stage": "failed", "error": result.get("error", "Unknown error")}
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
