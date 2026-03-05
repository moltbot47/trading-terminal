"""Tests for strategy_lab module — models, indicators, scanner."""

import json
import os
import sqlite3
import tempfile

import numpy as np
import pandas as pd
import pytest

from strategy_lab import indicators as ind
from strategy_lab import models


@pytest.fixture(autouse=True)
def tmp_db(monkeypatch, tmp_path):
    """Use a temp database for all tests."""
    db_path = str(tmp_path / "test_strategy_lab.db")
    monkeypatch.setattr(models, "_DB_PATH", db_path)
    models.init_db()
    return db_path


# --- Indicator Tests ---

def _make_ohlcv(n=100):
    """Generate synthetic OHLCV DataFrame."""
    np.random.seed(42)
    close = 100 + np.cumsum(np.random.randn(n) * 0.5)
    high = close + np.abs(np.random.randn(n) * 0.3)
    low = close - np.abs(np.random.randn(n) * 0.3)
    open_ = close + np.random.randn(n) * 0.2
    volume = np.random.randint(1000, 10000, n)
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)


class TestIndicators:
    def test_ema(self):
        df = _make_ohlcv()
        result = ind.ema(df["Close"], 9)
        assert len(result) == len(df)
        assert not np.isnan(result.iloc[-1])

    def test_sma(self):
        df = _make_ohlcv()
        result = ind.sma(df["Close"], 20)
        assert np.isnan(result.iloc[0])
        assert not np.isnan(result.iloc[-1])

    def test_rsi(self):
        df = _make_ohlcv()
        result = ind.rsi(df["Close"], 14)
        assert 0 <= result.iloc[-1] <= 100

    def test_macd(self):
        df = _make_ohlcv()
        result = ind.macd(df["Close"])
        assert "macd" in result
        assert "signal" in result
        assert "histogram" in result

    def test_atr(self):
        df = _make_ohlcv()
        result = ind.atr(df, 14)
        assert result.iloc[-1] > 0

    def test_adx(self):
        df = _make_ohlcv()
        result = ind.adx(df, 14)
        assert not np.isnan(result.iloc[-1])

    def test_bollinger_bands(self):
        df = _make_ohlcv()
        result = ind.bollinger_bands(df["Close"])
        assert result["upper"].iloc[-1] > result["middle"].iloc[-1]
        assert result["lower"].iloc[-1] < result["middle"].iloc[-1]

    def test_stochastic(self):
        df = _make_ohlcv()
        result = ind.stochastic(df)
        assert 0 <= result["k"].iloc[-1] <= 100

    def test_vwap(self):
        df = _make_ohlcv()
        result = ind.vwap(df)
        assert not np.isnan(result.iloc[-1])

    def test_compute_indicators(self):
        df = _make_ohlcv()
        configs = [
            {"indicator": "EMA", "params": {"period": 9}},
            {"indicator": "RSI", "params": {"period": 14}},
            {"indicator": "ADX", "params": {"period": 14}},
            {"indicator": "ATR", "params": {"period": 14}},
        ]
        result = ind.compute_indicators(df, configs)
        assert "EMA_9" in result
        assert "RSI_14" in result
        assert "ADX_14" in result
        assert "ATR_14" in result

    def test_evaluate_condition_static(self):
        df = _make_ohlcv()
        configs = [{"indicator": "RSI", "params": {"period": 14}}]
        computed = ind.compute_indicators(df, configs)

        rsi_val = float(computed["RSI_14"].iloc[-1])
        condition = {"indicator": "RSI", "params": {"period": 14}, "condition": "<", "value": 100}
        assert ind.evaluate_condition(condition, computed, df) is True

        condition2 = {"indicator": "RSI", "params": {"period": 14}, "condition": ">", "value": 100}
        assert ind.evaluate_condition(condition2, computed, df) is False

    def test_evaluate_condition_reference(self):
        df = _make_ohlcv()
        configs = [
            {"indicator": "EMA", "params": {"period": 9}},
            {"indicator": "EMA", "params": {"period": 50}},
        ]
        computed = ind.compute_indicators(df, configs)

        condition = {
            "indicator": "EMA", "params": {"period": 9},
            "condition": ">",
            "reference": {"indicator": "EMA", "params": {"period": 50}},
        }
        result = ind.evaluate_condition(condition, computed, df)
        assert isinstance(result, bool)


# --- Model Tests ---

class TestModels:
    def test_create_and_get_strategy(self):
        sid = models.create_strategy(
            name="Test EMA Cross",
            entry_rules=[{"indicator": "EMA", "params": {"period": 9}, "condition": "crosses_above",
                          "reference": {"indicator": "EMA", "params": {"period": 21}}}],
            exit_rules={"stop_loss": {"method": "fixed_points", "value": 20}},
        )
        assert sid > 0
        s = models.get_strategy(sid)
        assert s["name"] == "Test EMA Cross"
        assert s["active"] == 1

    def test_get_strategies(self):
        models.create_strategy(name="S1", entry_rules=[{"indicator": "RSI"}], exit_rules={})
        models.create_strategy(name="S2", entry_rules=[{"indicator": "ADX"}], exit_rules={})
        all_strats = models.get_strategies(active_only=False)
        assert len(all_strats) >= 2

    def test_toggle_strategy(self):
        sid = models.create_strategy(name="Toggle Test", entry_rules=[], exit_rules={})
        assert models.toggle_strategy(sid) is False  # was 1, now 0
        assert models.toggle_strategy(sid) is True   # back to 1

    def test_delete_strategy(self):
        sid = models.create_strategy(name="Delete Me", entry_rules=[], exit_rules={})
        models.delete_strategy(sid)
        assert models.get_strategy(sid) is None

    def test_create_and_close_hit(self):
        sid = models.create_strategy(name="Hit Test", entry_rules=[], exit_rules={})
        hid = models.create_hit(
            strategy_id=sid, instrument="MNQ", direction="long",
            entry_price=20000.0, stop_loss=19950.0, take_profit=20100.0,
        )
        assert hid > 0
        active = models.get_active_hits()
        assert any(h["id"] == hid for h in active)

        models.close_hit(hid, 20100.0, "take_profit")
        active = models.get_active_hits()
        assert not any(h["id"] == hid for h in active)

    def test_update_hit_tracking(self):
        sid = models.create_strategy(name="Track Test", entry_rules=[], exit_rules={})
        hid = models.create_hit(
            strategy_id=sid, instrument="MNQ", direction="long",
            entry_price=20000.0,
        )
        models.update_hit_tracking(hid, 20050.0)
        models.update_hit_tracking(hid, 19980.0)

        hits = models.get_recent_hits()
        hit = next(h for h in hits if h["id"] == hid)
        assert hit["mfe_points"] == 50.0
        assert hit["mae_points"] == -20.0
        assert hit["bars_held"] == 2

    def test_strategy_stats(self):
        sid = models.create_strategy(name="Stats Test", entry_rules=[], exit_rules={})
        h1 = models.create_hit(strategy_id=sid, instrument="MNQ", direction="long", entry_price=20000.0)
        models.close_hit(h1, 20050.0, "take_profit")
        h2 = models.create_hit(strategy_id=sid, instrument="MNQ", direction="long", entry_price=20000.0)
        models.close_hit(h2, 19950.0, "stop_loss")

        stats = models.get_strategy_stats(sid)
        assert stats["wins"] == 1
        assert stats["losses"] == 1
        assert stats["win_rate"] == 50.0
