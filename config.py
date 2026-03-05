"""Configuration for Trading Terminal v3."""

import os

# Project paths
PROJ: str = os.path.expanduser("~/latpfn-trading")
DATA: str = os.path.join(PROJ, "data")
if not os.path.isdir(DATA):
    DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
    os.makedirs(DATA, exist_ok=True)

# Server -- Railway sets PORT env var; fall back to 5099 for local dev
PORT: int = int(os.environ.get("PORT", "5099"))
HOST: str = "0.0.0.0" if os.environ.get("RAILWAY_ENVIRONMENT") else "127.0.0.1"  # nosec B104
DEBUG: bool = False

# Database -- Postgres on Railway, SQLite locally
DATABASE_URL: str = os.environ.get("DATABASE_URL", "")

# Single authoritative yfinance mapping -- futures + stocks
YF_MAP: dict[str, str] = {
    "MNQ": "NQ=F",
    "MYM": "YM=F",
    "MES": "ES=F",
    "MBT": "BTC=F",
    "QQQ": "QQQ",
    "TQQQ": "TQQQ",
    "SPY": "SPY",
    "SPXL": "SPXL",
    "NVDA": "NVDA",
    "TSLA": "TSLA",
    "AMD": "AMD",
}

# Instruments to track (futures only for live scanner)
INSTRUMENTS: list[str] = ["MNQ", "MYM", "MES", "MBT"]

# Stock tickers for price tracking (not scanned, just displayed)
STOCK_INSTRUMENTS: list[str] = ["QQQ", "TQQQ", "SPY", "SPXL", "NVDA", "TSLA", "AMD"]

# All instruments for price feed
ALL_INSTRUMENTS: list[str] = INSTRUMENTS + STOCK_INSTRUMENTS

# Cache TTLs (seconds)
SNAPSHOT_CACHE_TTL: float = 4.0
BARS_CACHE_TTL: float = 300.0
REGIME_CACHE_TTL: float = 60.0

# Rate limiting
RATE_LIMIT_WINDOW: int = 60  # seconds
RATE_LIMIT_MAX_REQUESTS: int = 120  # per window per IP

# Discord notifications
DISCORD_WEBHOOK_URL: str = os.environ.get("DISCORD_WEBHOOK_URL", "")

# Alpaca paper trading auto-execution
ALPACA_AUTO_TRADE: bool = bool(os.environ.get("ALPACA_AUTO_TRADE", ""))

# News filter config
NEWS_FILTER_CONFIG: dict = {
    "enabled": True,
    "currencies": ["USD"],
    "high_impact_window_minutes": 15,
    "medium_impact_window_minutes": 10,
    "cache_ttl_seconds": 300,
}

# CORS allowed origins
CORS_ORIGINS: list[str] = [
    "http://localhost:5099",
    "http://127.0.0.1:5099",
]
# Allow Railway domain if deployed
_railway_url = os.environ.get("RAILWAY_PUBLIC_DOMAIN")
if _railway_url:
    CORS_ORIGINS.append(f"https://{_railway_url}")
