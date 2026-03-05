"""Database models for Strategy Lab — strategies, scanner hits, simulated trades."""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

# Use latpfn data dir if available, otherwise local directory
_DATA_DIR = os.path.expanduser("~/latpfn-trading/data")
if not os.path.isdir(_DATA_DIR):
    _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(_DATA_DIR, exist_ok=True)
_DB_PATH = os.path.join(_DATA_DIR, "strategy_lab.db")
_db_lock = threading.Lock()


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    with _db_lock:
        conn = _get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS strategies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                source_url TEXT,
                source_type TEXT DEFAULT 'youtube',
                transcript TEXT,
                description TEXT,
                timeframe TEXT DEFAULT '5m',
                instruments TEXT DEFAULT '["MNQ","MYM","MES","MBT"]',
                entry_rules TEXT NOT NULL DEFAULT '[]',
                exit_rules TEXT NOT NULL DEFAULT '{}',
                direction_rules TEXT NOT NULL DEFAULT '[]',
                indicators_config TEXT NOT NULL DEFAULT '[]',
                risk_reward_target REAL DEFAULT 2.0,
                active INTEGER DEFAULT 1,
                total_scans INTEGER DEFAULT 0,
                total_hits INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scanner_hits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER NOT NULL REFERENCES strategies(id),
                timestamp TEXT NOT NULL,
                instrument TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry_price REAL NOT NULL,
                stop_loss REAL,
                take_profit REAL,
                confidence REAL,
                conditions_met TEXT,
                status TEXT DEFAULT 'detected',
                exit_price REAL,
                exit_timestamp TEXT,
                exit_reason TEXT,
                pnl_points REAL,
                pnl_dollars REAL,
                bars_held INTEGER DEFAULT 0,
                mae_points REAL DEFAULT 0,
                mfe_points REAL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_hits_strategy
                ON scanner_hits(strategy_id, timestamp);
            CREATE INDEX IF NOT EXISTS idx_hits_status
                ON scanner_hits(status);
            CREATE INDEX IF NOT EXISTS idx_hits_instrument
                ON scanner_hits(instrument, timestamp);
        """)
        conn.commit()
        conn.close()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# --- Strategy CRUD ---

def create_strategy(
    name: str,
    entry_rules: list,
    exit_rules: dict,
    direction_rules: list | None = None,
    indicators_config: list | None = None,
    description: str = "",
    source_url: str = "",
    source_type: str = "youtube",
    transcript: str = "",
    timeframe: str = "5m",
    instruments: list[str] | None = None,
    risk_reward_target: float = 2.0,
) -> int:
    """Insert a new strategy, return its ID."""
    if instruments is None:
        instruments = ["MNQ", "MYM", "MES", "MBT"]
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO strategies
               (name, source_url, source_type, transcript, description, timeframe,
                instruments, entry_rules, exit_rules, direction_rules,
                indicators_config, risk_reward_target)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                name, source_url, source_type, transcript, description, timeframe,
                json.dumps(instruments), json.dumps(entry_rules), json.dumps(exit_rules),
                json.dumps(direction_rules or []), json.dumps(indicators_config or []),
                risk_reward_target,
            ),
        )
        conn.commit()
        sid = cur.lastrowid
        conn.close()
    return sid


def get_strategies(active_only: bool = True) -> list[dict]:
    with _db_lock:
        conn = _get_conn()
        where = "WHERE active = 1" if active_only else ""
        rows = conn.execute(f"SELECT * FROM strategies {where} ORDER BY created_at DESC").fetchall()
        conn.close()
    return _rows_to_list(rows)


def get_strategy(strategy_id: int) -> dict | None:
    with _db_lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        conn.close()
    return _row_to_dict(row) if row else None


def toggle_strategy(strategy_id: int) -> bool:
    """Toggle active state. Returns new state."""
    with _db_lock:
        conn = _get_conn()
        conn.execute("UPDATE strategies SET active = 1 - active WHERE id = ?", (strategy_id,))
        conn.commit()
        row = conn.execute("SELECT active FROM strategies WHERE id = ?", (strategy_id,)).fetchone()
        conn.close()
    return bool(row["active"]) if row else False


def delete_strategy(strategy_id: int) -> None:
    with _db_lock:
        conn = _get_conn()
        conn.execute("DELETE FROM scanner_hits WHERE strategy_id = ?", (strategy_id,))
        conn.execute("DELETE FROM strategies WHERE id = ?", (strategy_id,))
        conn.commit()
        conn.close()


def increment_scan_count(strategy_id: int, hit: bool = False) -> None:
    with _db_lock:
        conn = _get_conn()
        if hit:
            conn.execute(
                "UPDATE strategies SET total_scans = total_scans + 1, total_hits = total_hits + 1 WHERE id = ?",
                (strategy_id,),
            )
        else:
            conn.execute(
                "UPDATE strategies SET total_scans = total_scans + 1 WHERE id = ?",
                (strategy_id,),
            )
        conn.commit()
        conn.close()


# --- Scanner Hits CRUD ---

def create_hit(
    strategy_id: int,
    instrument: str,
    direction: str,
    entry_price: float,
    stop_loss: float | None = None,
    take_profit: float | None = None,
    confidence: float | None = None,
    conditions_met: list | None = None,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = _get_conn()
        cur = conn.execute(
            """INSERT INTO scanner_hits
               (strategy_id, timestamp, instrument, direction, entry_price,
                stop_loss, take_profit, confidence, conditions_met, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'simulating')""",
            (
                strategy_id, now, instrument, direction, entry_price,
                stop_loss, take_profit, confidence,
                json.dumps(conditions_met or []),
            ),
        )
        conn.commit()
        hit_id = cur.lastrowid
        conn.close()
    increment_scan_count(strategy_id, hit=True)
    return hit_id


def update_hit_tracking(hit_id: int, current_price: float) -> None:
    """Update MAE/MFE and bars_held for an active simulated trade."""
    with _db_lock:
        conn = _get_conn()
        row = conn.execute(
            "SELECT * FROM scanner_hits WHERE id = ? AND status = 'simulating'",
            (hit_id,),
        ).fetchone()
        if not row:
            conn.close()
            return
        entry = row["entry_price"]
        direction = row["direction"]
        mae = row["mae_points"] or 0.0
        mfe = row["mfe_points"] or 0.0
        bars = (row["bars_held"] or 0) + 1

        if direction == "long":
            excursion = current_price - entry
        else:
            excursion = entry - current_price

        new_mfe = max(mfe, excursion)
        new_mae = min(mae, excursion)

        conn.execute(
            "UPDATE scanner_hits SET mae_points = ?, mfe_points = ?, bars_held = ? WHERE id = ?",
            (new_mae, new_mfe, bars, hit_id),
        )
        conn.commit()
        conn.close()


def close_hit(hit_id: int, exit_price: float, exit_reason: str) -> None:
    """Close a simulated trade with final P&L."""
    now = datetime.now(timezone.utc).isoformat()
    with _db_lock:
        conn = _get_conn()
        row = conn.execute("SELECT * FROM scanner_hits WHERE id = ?", (hit_id,)).fetchone()
        if not row:
            conn.close()
            return
        entry = row["entry_price"]
        direction = row["direction"]
        if direction == "long":
            pnl_pts = exit_price - entry
        else:
            pnl_pts = entry - exit_price

        status = "won" if pnl_pts > 0 else "lost" if pnl_pts < 0 else "breakeven"
        conn.execute(
            """UPDATE scanner_hits SET status = ?, exit_price = ?, exit_timestamp = ?,
               exit_reason = ?, pnl_points = ? WHERE id = ?""",
            (status, exit_price, now, exit_reason, round(pnl_pts, 4), hit_id),
        )
        conn.commit()
        conn.close()


def get_active_hits() -> list[dict]:
    with _db_lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT * FROM scanner_hits WHERE status = 'simulating' ORDER BY timestamp DESC"
        ).fetchall()
        conn.close()
    return _rows_to_list(rows)


def get_recent_hits(limit: int = 50) -> list[dict]:
    with _db_lock:
        conn = _get_conn()
        rows = conn.execute(
            "SELECT h.*, s.name as strategy_name FROM scanner_hits h "
            "JOIN strategies s ON h.strategy_id = s.id "
            "ORDER BY h.timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    return _rows_to_list(rows)


def get_strategy_stats(strategy_id: int | None = None) -> dict:
    """Aggregate stats for one or all strategies."""
    with _db_lock:
        conn = _get_conn()
        where = "WHERE strategy_id = ?" if strategy_id else ""
        params = (strategy_id,) if strategy_id else ()

        total = conn.execute(
            f"SELECT COUNT(*) as c FROM scanner_hits {where}", params
        ).fetchone()["c"]
        closed = conn.execute(
            f"SELECT COUNT(*) as c FROM scanner_hits {where} {'AND' if where else 'WHERE'} status IN ('won','lost','breakeven')",
            params,
        ).fetchone()["c"]
        wins = conn.execute(
            f"SELECT COUNT(*) as c FROM scanner_hits {where} {'AND' if where else 'WHERE'} status = 'won'",
            params,
        ).fetchone()["c"]
        losses = conn.execute(
            f"SELECT COUNT(*) as c FROM scanner_hits {where} {'AND' if where else 'WHERE'} status = 'lost'",
            params,
        ).fetchone()["c"]
        total_pnl = conn.execute(
            f"SELECT COALESCE(SUM(pnl_points), 0) as s FROM scanner_hits {where} {'AND' if where else 'WHERE'} status IN ('won','lost','breakeven')",
            params,
        ).fetchone()["s"]
        avg_win = conn.execute(
            f"SELECT COALESCE(AVG(pnl_points), 0) as a FROM scanner_hits {where} {'AND' if where else 'WHERE'} status = 'won'",
            params,
        ).fetchone()["a"]
        avg_loss = conn.execute(
            f"SELECT COALESCE(AVG(pnl_points), 0) as a FROM scanner_hits {where} {'AND' if where else 'WHERE'} status = 'lost'",
            params,
        ).fetchone()["a"]
        conn.close()

    win_rate = round(wins / closed * 100, 1) if closed > 0 else 0
    gross_win = avg_win * wins if wins else 0
    gross_loss = abs(avg_loss * losses) if losses else 1
    profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0

    return {
        "total_hits": total,
        "closed": closed,
        "active": total - closed,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_pnl_points": round(total_pnl, 4),
        "avg_win": round(avg_win, 4),
        "avg_loss": round(avg_loss, 4),
        "profit_factor": profit_factor,
    }
