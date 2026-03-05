"""Shared test fixtures for trading terminal tests."""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

# Ensure the trading-terminal directory is FIRST in sys.path
# so our config.py wins over ~/latpfn-trading/config/ package
_PROJECT_DIR = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.abspath(_PROJECT_DIR))

# ---------------------------------------------------------------------------
# Mock external latpfn-trading modules so tests run without that repo present
# (e.g., in CI on GitHub Actions). Only mock if not already importable.
# ---------------------------------------------------------------------------

_EXTERNAL_MODULES = {
    "signals": types.ModuleType("signals"),
    "signals.news_filter": types.ModuleType("signals.news_filter"),
    "signals.regime": types.ModuleType("signals.regime"),
    "strategies": types.ModuleType("strategies"),
    "strategies.trend_follower": types.ModuleType("strategies.trend_follower"),
    "strategies.trend_follower.price_feed": types.ModuleType("strategies.trend_follower.price_feed"),
}


def _ensure_mocks() -> None:
    """Install mock modules for any external deps not available."""
    for mod_name, mod in _EXTERNAL_MODULES.items():
        if mod_name not in sys.modules:
            sys.modules[mod_name] = mod

    # Provide mock classes/functions that app.py imports
    pf_mod = sys.modules["strategies.trend_follower.price_feed"]
    if not hasattr(pf_mod, "PriceFeed"):
        mock_feed = MagicMock()
        mock_feed.snapshot.return_value = {}
        mock_feed.full_bars.return_value = {}
        mock_feed._last_snapshots = {}
        mock_feed._snapshot_cache_time = 0
        mock_feed._ticker_to_inst = {}

        PriceFeedClass = MagicMock(return_value=mock_feed)
        PriceFeedClass.snapshot = MagicMock()
        PriceFeedClass.full_bars = MagicMock()
        pf_mod.PriceFeed = PriceFeedClass
        pf_mod.PriceSnapshot = MagicMock
        pf_mod.TICKER_MAP = {"MNQ": "NQ=F", "MYM": "YM=F", "MES": "ES=F", "MBT": "BTC=F"}

    nf_mod = sys.modules["signals.news_filter"]
    if not hasattr(nf_mod, "NewsFilter"):
        mock_nf = MagicMock()
        mock_nf.fetch_calendar.return_value = []
        nf_mod.NewsFilter = MagicMock(return_value=mock_nf)

    regime_mod = sys.modules["signals.regime"]
    if not hasattr(regime_mod, "detect_regime"):
        regime_mod.detect_regime = MagicMock(return_value={"regime": "ranging", "adx": 15.0})


_ensure_mocks()


@pytest.fixture
def client():
    """Create a Flask test client."""
    from app import app
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
