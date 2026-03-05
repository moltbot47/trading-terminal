"""Tests for configuration values."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import CORS_ORIGINS, INSTRUMENTS, YF_MAP


def test_yf_map_has_all_symbols():
    """YF_MAP should contain all four expected instrument mappings."""
    expected = {"MNQ": "NQ=F", "MYM": "YM=F", "MES": "ES=F", "MBT": "BTC=F"}
    assert expected == YF_MAP


def test_yf_map_keys_match_instruments():
    """YF_MAP keys should match the INSTRUMENTS list."""
    assert set(YF_MAP.keys()) == set(INSTRUMENTS)


def test_instruments_list():
    """INSTRUMENTS should contain the four expected symbols."""
    assert sorted(INSTRUMENTS) == ["MBT", "MES", "MNQ", "MYM"]


def test_cors_origins_localhost_only():
    """CORS origins should only include localhost."""
    for origin in CORS_ORIGINS:
        assert "localhost" in origin or "127.0.0.1" in origin
