-- schema.sql — Postgres DDL for trading-terminal cloud database
-- Run once via: psql $DATABASE_URL -f schema.sql
-- Or call db.init_schema() from Python.

-- ============================================================
-- broker_reports.db
-- ============================================================

CREATE TABLE IF NOT EXISTS broker_snapshots (
    id              SERIAL PRIMARY KEY,
    report_date     TEXT UNIQUE,
    actual_balance  REAL,
    actual_pnl_today REAL,
    actual_total_pnl REAL,
    actual_trades_today INTEGER,
    commissions     REAL DEFAULT 0,
    notes           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS broker_trades (
    id              SERIAL PRIMARY KEY,
    report_date     TEXT,
    timestamp       TEXT,
    instrument      TEXT,
    direction       TEXT,
    quantity        INTEGER,
    entry_price     REAL,
    exit_price      REAL,
    pnl             REAL,
    commission      REAL DEFAULT 0,
    order_id        TEXT,
    raw_symbol      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- trade_log.db
-- ============================================================

CREATE TABLE IF NOT EXISTS predictions (
    id                  SERIAL PRIMARY KEY,
    timestamp           TEXT,
    cycle               TEXT,
    instrument          TEXT,
    direction           TEXT,
    model_confidence    REAL,
    trend_clarity       REAL,
    uncertainty_inverse REAL,
    composite_confidence REAL,
    regime              TEXT,
    shot_tier           TEXT,
    current_price       REAL,
    forecast_end_price  REAL,
    signal_generated    INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_pred_instrument ON predictions(instrument, timestamp);

CREATE TABLE IF NOT EXISTS trades (
    id                  SERIAL PRIMARY KEY,
    prediction_id       INTEGER REFERENCES predictions(id),
    timestamp           TEXT,
    instrument          TEXT,
    direction           TEXT,
    shot_tier           TEXT,
    entry_price         REAL,
    stop_loss           REAL,
    take_profit         REAL,
    position_size       REAL,
    contract_size       REAL,
    risk_dollars        REAL,
    reward_dollars      REAL,
    rr_ratio            REAL,
    regime              TEXT,
    drawdown_cushion    REAL,
    effective_risk_pct  REAL,
    execution_status    TEXT DEFAULT 'dry_run',
    rejection_reason    TEXT,
    exit_price          REAL,
    exit_reason         TEXT,
    pnl_dollars         REAL,
    mae_points          REAL,
    mfe_points          REAL,
    time_in_trade_minutes REAL
);

CREATE INDEX IF NOT EXISTS idx_trade_instrument ON trades(instrument);
CREATE INDEX IF NOT EXISTS idx_trade_status ON trades(execution_status);

-- ============================================================
-- polymarket_forecasts.db
-- ============================================================

CREATE TABLE IF NOT EXISTS forecasts (
    id              SERIAL PRIMARY KEY,
    market_id       TEXT,
    question        TEXT,
    llm_probability REAL,
    llm_confidence  REAL,
    market_price    REAL,
    reasoning       TEXT,
    model           TEXT,
    timestamp       TEXT,
    outcome         TEXT DEFAULT NULL,
    resolved_at     TEXT DEFAULT NULL
);

CREATE INDEX IF NOT EXISTS idx_forecasts_market ON forecasts(market_id);

-- ============================================================
-- turbo_analytics.db
-- ============================================================

CREATE TABLE IF NOT EXISTS turbo_signals (
    id                  SERIAL PRIMARY KEY,
    timestamp           TEXT,
    asset               TEXT,
    timeframe           TEXT,
    window_start_ts     BIGINT,
    window_end_ts       BIGINT,
    seconds_left        REAL,
    up_price            REAL,
    down_price          REAL,
    crypto_price        REAL,
    pct_change_1m       REAL,
    pct_change_3m       REAL,
    momentum_strength   REAL,
    momentum_direction  TEXT,
    signal_generated    INTEGER DEFAULT 0,
    signal_type         TEXT,
    signal_direction    TEXT,
    signal_reason       TEXT,
    skip_reason         TEXT,
    traded              INTEGER DEFAULT 0,
    entry_price         REAL,
    shares              REAL,
    size_usdc           REAL,
    order_id            TEXT,
    outcome             TEXT,
    result              TEXT,
    pnl                 REAL,
    shadow_direction    TEXT,
    shadow_entry_price  REAL,
    shadow_outcome      TEXT,
    shadow_pnl          REAL,
    intel_convergence   REAL,
    intel_multiplier    REAL
);

CREATE INDEX IF NOT EXISTS idx_turbo_timestamp ON turbo_signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_turbo_asset ON turbo_signals(asset, timestamp);
CREATE INDEX IF NOT EXISTS idx_turbo_traded ON turbo_signals(traded);

-- ============================================================
-- strategy_lab.db
-- ============================================================

CREATE TABLE IF NOT EXISTS strategies (
    id                  SERIAL PRIMARY KEY,
    name                TEXT NOT NULL,
    source_url          TEXT,
    source_type         TEXT DEFAULT 'youtube',
    transcript          TEXT,
    description         TEXT,
    timeframe           TEXT DEFAULT '5m',
    instruments         TEXT DEFAULT '["MNQ","MYM","MES","MBT"]',
    entry_rules         TEXT NOT NULL DEFAULT '[]',
    exit_rules          TEXT NOT NULL DEFAULT '{}',
    direction_rules     TEXT NOT NULL DEFAULT '[]',
    indicators_config   TEXT NOT NULL DEFAULT '[]',
    risk_reward_target  REAL DEFAULT 2.0,
    active              INTEGER DEFAULT 1,
    total_scans         INTEGER DEFAULT 0,
    total_hits          INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS scanner_hits (
    id              SERIAL PRIMARY KEY,
    strategy_id     INTEGER NOT NULL REFERENCES strategies(id),
    timestamp       TEXT NOT NULL,
    instrument      TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    stop_loss       REAL,
    take_profit     REAL,
    confidence      REAL,
    conditions_met  TEXT,
    status          TEXT DEFAULT 'detected',
    exit_price      REAL,
    exit_timestamp  TEXT,
    exit_reason     TEXT,
    pnl_points      REAL,
    pnl_dollars     REAL,
    bars_held       INTEGER DEFAULT 0,
    mae_points      REAL DEFAULT 0,
    mfe_points      REAL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_hits_strategy ON scanner_hits(strategy_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_hits_status ON scanner_hits(status);
CREATE INDEX IF NOT EXISTS idx_hits_instrument ON scanner_hits(instrument, timestamp);

-- ============================================================
-- JSON state store (positions, heartbeat, etc.)
-- ============================================================

CREATE TABLE IF NOT EXISTS json_state (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
