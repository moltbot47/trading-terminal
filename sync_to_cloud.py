#!/usr/bin/env python3
"""Background sync daemon: pushes local SQLite data + JSON files to Postgres.

Run on the Mac alongside the trading system:
    DATABASE_URL=postgres://... python sync_to_cloud.py

State is tracked in ~/.trading-sync-state.json so restarts resume where they
left off.
"""

import contextlib
import json
import logging
import os
import signal
import sqlite3
import sys
import time
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DATA_DIR = Path.home() / "latpfn-trading" / "data"
STATE_FILE = Path.home() / ".trading-sync-state.json"

# (sqlite_db_filename, table_name, interval_seconds)
SYNC_TABLES: list[tuple[str, str, int]] = [
    ("broker_reports.db", "broker_trades", 60),
    ("broker_reports.db", "broker_snapshots", 60),
    ("trade_log.db", "predictions", 60),
    ("trade_log.db", "trades", 60),
    ("polymarket_forecasts.db", "forecasts", 60),
    ("turbo_analytics.db", "turbo_signals", 30),
    ("strategy_lab.db", "strategies", 60),
    ("strategy_lab.db", "scanner_hits", 60),
]

# JSON files to mirror into json_state
JSON_FILES: list[str] = [
    "positions.json",
    "trend_positions.json",
    "heartbeat.json",
    "drawdown_state.json",
    "system_state.json",
]
JSON_INTERVAL = 10  # seconds

BATCH_LIMIT = 500
LOOP_SLEEP = 5

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("sync")

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_running = True


def _handle_signal(signum, frame):
    global _running
    log.info("Caught signal %s — shutting down gracefully", signum)
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            log.warning("Corrupt state file, starting fresh")
    return {}


def save_state(state: dict) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        log.exception("Failed to save state file")


# ---------------------------------------------------------------------------
# Postgres helpers
# ---------------------------------------------------------------------------


def pg_connect() -> psycopg.Connection:
    """Open a single Postgres connection (no pool needed for sync daemon)."""
    return psycopg.connect(DATABASE_URL, autocommit=False)


# ---------------------------------------------------------------------------
# SQLite -> Postgres table sync
# ---------------------------------------------------------------------------


def _sqlite_conn(db_file: str) -> sqlite3.Connection | None:
    path = DATA_DIR / db_file
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def sync_table(
    pg: psycopg.Connection,
    db_file: str,
    table: str,
    last_id: int,
) -> int:
    """Sync rows from SQLite where id > last_id. Returns new last_id."""
    sq = _sqlite_conn(db_file)
    if sq is None:
        return last_id

    try:
        rows = sq.execute(
            f"SELECT * FROM {table} WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, BATCH_LIMIT),
        ).fetchall()

        if not rows:
            sq.close()
            return last_id

        # Build column list from first row
        columns = rows[0].keys()
        col_list = ", ".join(columns)
        placeholders = ", ".join(["%s"] * len(columns))

        insert_sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT (id) DO NOTHING"
        )

        new_last_id = last_id
        all_values = []
        for row in rows:
            values = tuple(row[c] for c in columns)
            all_values.append(values)
            rid = row["id"]
            if rid > new_last_id:
                new_last_id = rid
        with pg.cursor() as cur:
            cur.executemany(insert_sql, all_values)
        pg.commit()

        count = len(rows)
        if count > 0:
            log.info(
                "  %s.%s: synced %d rows (id %d -> %d)",
                db_file, table, count, last_id, new_last_id,
            )

        sq.close()
        return new_last_id

    except Exception:
        log.exception("Error syncing %s.%s", db_file, table)
        with contextlib.suppress(Exception):
            pg.rollback()
        if sq:
            sq.close()
        return last_id


# ---------------------------------------------------------------------------
# JSON file -> json_state sync
# ---------------------------------------------------------------------------


def sync_json(pg: psycopg.Connection, filename: str) -> bool:
    """Read a JSON file and upsert it into json_state. Returns True on success."""
    path = DATA_DIR / filename
    if not path.exists():
        return False

    try:
        raw = path.read_text().strip()
        if not raw:
            return False
        data = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return False

    key = filename.replace(".json", "")

    try:
        with pg.cursor() as cur:
            cur.execute(
                """INSERT INTO json_state (key, value, updated_at)
                   VALUES (%s, %s, NOW())
                   ON CONFLICT (key)
                   DO UPDATE SET value = EXCLUDED.value,
                                 updated_at = NOW()""",
                (key, Jsonb(data)),
            )
        pg.commit()
        return True
    except Exception:
        log.exception("Error syncing JSON %s", filename)
        with contextlib.suppress(Exception):
            pg.rollback()
        return False


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def print_summary(state: dict) -> None:
    log.info("=" * 60)
    log.info("Sync daemon state summary:")
    for entry in SYNC_TABLES:
        db_file, table, _ = entry
        state_key = f"{db_file}:{table}"
        last_id = state.get(state_key, 0)
        log.info("  %-40s  last_id = %d", state_key, last_id)
    log.info("  JSON files: %s", ", ".join(JSON_FILES))
    log.info("  Data dir:   %s", DATA_DIR)
    log.info("  State file: %s", STATE_FILE)
    log.info("=" * 60)


def main() -> None:
    if not DATABASE_URL:
        log.error("DATABASE_URL is not set. Export it and re-run.")
        sys.exit(1)

    if not DATA_DIR.exists():
        log.error("Data directory does not exist: %s", DATA_DIR)
        sys.exit(1)

    log.info("Connecting to Postgres...")
    try:
        pg = pg_connect()
    except Exception:
        log.exception("Cannot connect to Postgres")
        sys.exit(1)
    log.info("Connected to Postgres")

    state = load_state()
    print_summary(state)

    # Track last-sync times per table and for JSON
    last_sync_times: dict[str, float] = {}
    last_json_sync = 0.0
    stats_interval = 300  # print summary every 5 min
    last_stats_time = time.time()
    total_rows_synced = 0
    total_json_synced = 0

    while _running:
        now = time.time()

        # --- Table syncs ---
        for db_file, table, interval in SYNC_TABLES:
            state_key = f"{db_file}:{table}"
            last_t = last_sync_times.get(state_key, 0.0)

            if now - last_t < interval:
                continue

            last_id = state.get(state_key, 0)
            new_last_id = sync_table(pg, db_file, table, last_id)

            if new_last_id != last_id:
                total_rows_synced += new_last_id - last_id
                state[state_key] = new_last_id
                save_state(state)

            last_sync_times[state_key] = now

        # --- JSON syncs ---
        if now - last_json_sync >= JSON_INTERVAL:
            for fname in JSON_FILES:
                if sync_json(pg, fname):
                    total_json_synced += 1
            last_json_sync = now

        # --- Periodic stats ---
        if now - last_stats_time >= stats_interval:
            log.info(
                "Stats: %d rows synced, %d JSON upserts since start",
                total_rows_synced,
                total_json_synced,
            )
            last_stats_time = now

        # --- Sleep ---
        time.sleep(LOOP_SLEEP)

    # Cleanup
    log.info("Shutting down. Final stats: %d rows, %d JSON upserts", total_rows_synced, total_json_synced)
    save_state(state)
    with contextlib.suppress(Exception):
        pg.close()
    log.info("Done.")


if __name__ == "__main__":
    main()
