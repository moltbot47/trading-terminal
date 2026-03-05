"""Database models for backtester — runs, trades, and cached bars.

Supports both PostgreSQL (when DATABASE_URL is set) and SQLite (local dev).
"""

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = bool(_DATABASE_URL)

# SQLite setup (local fallback)
DATA_DIR = Path(os.environ.get("DATA", str(Path.home() / "latpfn-trading" / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
_DB_PATH = str(DATA_DIR / "backtest.db")
_db_lock = threading.Lock()


def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _pg_conn():
    """Get a Postgres connection from the shared pool."""
    import db as _db
    return _db.get_pool().connection()


def _to_dicts(cur, rows) -> list[dict[str, Any]]:
    if not rows:
        return []
    if hasattr(rows[0], "keys"):
        return [dict(r) for r in rows]
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=False)) for r in rows]


def _to_dict(cur, row) -> dict[str, Any] | None:
    if row is None:
        return None
    if hasattr(row, "keys"):
        return dict(row)
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, row, strict=False))


def _row_to_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return dict(row)
    return row if isinstance(row, dict) else {}


def _rows_to_list(rows: list) -> list[dict[str, Any]]:
    return [_row_to_dict(r) for r in rows]


def _q(sql: str) -> str:
    """Convert ? placeholders to %s for Postgres."""
    if _USE_PG:
        return sql.replace("?", "%s")
    return sql


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_backtest_db() -> None:
    """Create SQLite tables if they don't exist. Postgres uses schema.sql."""
    if _USE_PG:
        return
    with _db_lock:
        conn = _get_sqlite_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS backtest_bars (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume INTEGER,
                UNIQUE(symbol, timeframe, timestamp)
            );

            CREATE TABLE IF NOT EXISTS backtest_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy_id INTEGER,
                symbol TEXT,
                timeframe TEXT DEFAULT '5Min',
                start_date TEXT,
                end_date TEXT,
                status TEXT DEFAULT 'running',
                total_trades INTEGER DEFAULT 0,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                win_rate REAL,
                total_pnl REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                avg_trades_per_day REAL DEFAULT 0,
                equity_curve TEXT DEFAULT '[]',
                created_at TEXT,
                completed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS backtest_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER,
                entry_time TEXT,
                exit_time TEXT,
                direction TEXT,
                entry_price REAL,
                exit_price REAL,
                stop_loss REAL,
                take_profit REAL,
                pnl_points REAL DEFAULT 0,
                exit_reason TEXT,
                mae_points REAL DEFAULT 0,
                mfe_points REAL DEFAULT 0,
                bars_held INTEGER DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_bars_lookup
                ON backtest_bars(symbol, timeframe, timestamp);
            CREATE INDEX IF NOT EXISTS idx_trades_run
                ON backtest_trades(run_id);
        """)
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Runs CRUD
# ---------------------------------------------------------------------------

def create_run(
    strategy_id: int,
    symbol: str,
    timeframe: str,
    start_date: str,
    end_date: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    params = (strategy_id, symbol, timeframe, start_date, end_date, "running", now)
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(
                """INSERT INTO backtest_runs
                   (strategy_id, symbol, timeframe, start_date, end_date, status, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                params,
            )
            run_id = cur.fetchone()[0]
            conn.commit()
        return run_id
    with _db_lock:
        conn = _get_sqlite_conn()
        cur = conn.execute(
            """INSERT INTO backtest_runs
               (strategy_id, symbol, timeframe, start_date, end_date, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            params,
        )
        conn.commit()
        run_id = cur.lastrowid
        conn.close()
    return run_id


def update_run(run_id: int, **kwargs) -> None:
    """Update any fields on a backtest run."""
    if not kwargs:
        return
    # Serialize equity_curve if present
    if "equity_curve" in kwargs and not isinstance(kwargs["equity_curve"], str):
        kwargs["equity_curve"] = json.dumps(kwargs["equity_curve"])

    set_parts = []
    values = []
    for k, v in kwargs.items():
        set_parts.append(f"{k} = ?")
        values.append(v)
    values.append(run_id)
    sql = f"UPDATE backtest_runs SET {', '.join(set_parts)} WHERE id = ?"

    if _USE_PG:
        with _pg_conn() as conn:
            conn.execute(_q(sql), tuple(values))
            conn.commit()
        return
    with _db_lock:
        conn = _get_sqlite_conn()
        conn.execute(sql, tuple(values))
        conn.commit()
        conn.close()


def get_run(run_id: int) -> dict | None:
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute("SELECT * FROM backtest_runs WHERE id = %s", (run_id,))
            return _to_dict(cur, cur.fetchone())
    with _db_lock:
        conn = _get_sqlite_conn()
        row = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
        conn.close()
    return _row_to_dict(row) if row else None


def get_runs(limit: int = 20) -> list[dict]:
    sql = _q("SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT ?")
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(sql, (limit,))
            return _to_dicts(cur, cur.fetchall())
    with _db_lock:
        conn = _get_sqlite_conn()
        rows = conn.execute(sql, (limit,)).fetchall()
        conn.close()
    return _rows_to_list(rows)


def delete_run(run_id: int) -> None:
    """Delete a run and all its trades."""
    if _USE_PG:
        with _pg_conn() as conn:
            conn.execute("DELETE FROM backtest_trades WHERE run_id = %s", (run_id,))
            conn.execute("DELETE FROM backtest_runs WHERE id = %s", (run_id,))
            conn.commit()
        return
    with _db_lock:
        conn = _get_sqlite_conn()
        conn.execute("DELETE FROM backtest_trades WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM backtest_runs WHERE id = ?", (run_id,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Trades CRUD
# ---------------------------------------------------------------------------

def create_trade(
    run_id: int,
    entry_time: str,
    direction: str,
    entry_price: float,
    stop_loss: float | None,
    take_profit: float | None,
) -> int:
    params = (run_id, entry_time, direction, entry_price, stop_loss, take_profit)
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(
                """INSERT INTO backtest_trades
                   (run_id, entry_time, direction, entry_price, stop_loss, take_profit)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                params,
            )
            trade_id = cur.fetchone()[0]
            conn.commit()
        return trade_id
    with _db_lock:
        conn = _get_sqlite_conn()
        cur = conn.execute(
            """INSERT INTO backtest_trades
               (run_id, entry_time, direction, entry_price, stop_loss, take_profit)
               VALUES (?, ?, ?, ?, ?, ?)""",
            params,
        )
        conn.commit()
        trade_id = cur.lastrowid
        conn.close()
    return trade_id


def close_trade(
    trade_id: int,
    exit_time: str,
    exit_price: float,
    exit_reason: str,
    pnl_points: float,
    mae: float,
    mfe: float,
    bars_held: int,
) -> None:
    params = (exit_time, exit_price, exit_reason, round(pnl_points, 4),
              round(mae, 4), round(mfe, 4), bars_held, trade_id)
    sql = """UPDATE backtest_trades SET exit_time = ?, exit_price = ?,
             exit_reason = ?, pnl_points = ?, mae_points = ?, mfe_points = ?,
             bars_held = ? WHERE id = ?"""
    if _USE_PG:
        with _pg_conn() as conn:
            conn.execute(_q(sql), params)
            conn.commit()
        return
    with _db_lock:
        conn = _get_sqlite_conn()
        conn.execute(sql, params)
        conn.commit()
        conn.close()


def get_trades(run_id: int) -> list[dict]:
    sql = _q("SELECT * FROM backtest_trades WHERE run_id = ? ORDER BY entry_time ASC")
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(sql, (run_id,))
            return _to_dicts(cur, cur.fetchall())
    with _db_lock:
        conn = _get_sqlite_conn()
        rows = conn.execute(sql, (run_id,)).fetchall()
        conn.close()
    return _rows_to_list(rows)


# ---------------------------------------------------------------------------
# Bar cache
# ---------------------------------------------------------------------------

def cache_bars(symbol: str, timeframe: str, bars: list[dict]) -> None:
    """INSERT OR IGNORE bars into the cache."""
    if not bars:
        return
    if _USE_PG:
        with _pg_conn() as conn:
            for b in bars:
                conn.execute(
                    """INSERT INTO backtest_bars (symbol, timeframe, timestamp, open, high, low, close, volume)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, timeframe, timestamp) DO NOTHING""",
                    (symbol, timeframe, b["timestamp"], b["open"], b["high"],
                     b["low"], b["close"], b.get("volume", 0)),
                )
            conn.commit()
        return
    with _db_lock:
        conn = _get_sqlite_conn()
        conn.executemany(
            """INSERT OR IGNORE INTO backtest_bars
               (symbol, timeframe, timestamp, open, high, low, close, volume)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (symbol, timeframe, b["timestamp"], b["open"], b["high"],
                 b["low"], b["close"], b.get("volume", 0))
                for b in bars
            ],
        )
        conn.commit()
        conn.close()


def load_cached_bars(
    symbol: str, timeframe: str, start: str, end: str
) -> list[dict]:
    sql = _q(
        """SELECT timestamp, open, high, low, close, volume
           FROM backtest_bars
           WHERE symbol = ? AND timeframe = ? AND timestamp >= ? AND timestamp <= ?
           ORDER BY timestamp ASC"""
    )
    params = (symbol, timeframe, start, end)
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(sql, params)
            return _to_dicts(cur, cur.fetchall())
    with _db_lock:
        conn = _get_sqlite_conn()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
    return _rows_to_list(rows)


def get_cached_range(symbol: str, timeframe: str) -> tuple[str | None, str | None]:
    """Return (min_timestamp, max_timestamp) for cached bars, or (None, None)."""
    sql = _q(
        "SELECT MIN(timestamp), MAX(timestamp) FROM backtest_bars WHERE symbol = ? AND timeframe = ?"
    )
    params = (symbol, timeframe)
    if _USE_PG:
        with _pg_conn() as conn:
            cur = conn.execute(sql, params)
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, None)
    with _db_lock:
        conn = _get_sqlite_conn()
        row = conn.execute(sql, params).fetchone()
        conn.close()
    if row:
        return (row[0], row[1])
    return (None, None)
