"""Configuration for Trading Terminal v3."""

import os

# Project paths
PROJ: str = os.path.expanduser("~/latpfn-trading")
DATA: str = os.path.join(PROJ, "data")

# Server
PORT: int = 5099
HOST: str = "127.0.0.1"
DEBUG: bool = False

# Single authoritative yfinance mapping -- use futures contracts to match Tradovate
YF_MAP: dict[str, str] = {
    "MNQ": "NQ=F",
    "MYM": "YM=F",
    "MES": "ES=F",
    "MBT": "BTC=F",
}

# Instruments to track
INSTRUMENTS: list[str] = ["MNQ", "MYM", "MES", "MBT"]

# Cache TTLs (seconds)
SNAPSHOT_CACHE_TTL: float = 4.0
BARS_CACHE_TTL: float = 300.0
REGIME_CACHE_TTL: float = 60.0

# Rate limiting
RATE_LIMIT_WINDOW: int = 60  # seconds
RATE_LIMIT_MAX_REQUESTS: int = 120  # per window per IP

# News filter config
NEWS_FILTER_CONFIG: dict = {
    "enabled": True,
    "currencies": ["USD"],
    "high_impact_window_minutes": 15,
    "medium_impact_window_minutes": 10,
    "cache_ttl_seconds": 300,
}

# CORS allowed origins (localhost only)
CORS_ORIGINS: list[str] = [
    "http://localhost:5099",
    "http://127.0.0.1:5099",
]
