"""Alpaca historical data fetcher with local bar caching."""

import logging
import os
import time
from datetime import datetime

import pandas as pd

from backtest.models import cache_bars, load_cached_bars

logger = logging.getLogger(__name__)

# Lazy import Alpaca — may not be installed on all environments
_alpaca_client = None


def _get_alpaca_client():
    global _alpaca_client
    if _alpaca_client is None:
        from alpaca.data.historical import StockHistoricalDataClient
        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if api_key and secret_key:
            _alpaca_client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)
        else:
            raise ValueError(
                "ALPACA_API_KEY and ALPACA_SECRET_KEY required. "
                "Sign up free at https://alpaca.markets → Paper Trading → API Keys"
            )
    return _alpaca_client


def _get_timeframe_map():
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    return {
        "1Min": TimeFrame(1, TimeFrameUnit.Minute),
        "5Min": TimeFrame(5, TimeFrameUnit.Minute),
        "15Min": TimeFrame(15, TimeFrameUnit.Minute),
        "1Hour": TimeFrame(1, TimeFrameUnit.Hour),
        "1Day": TimeFrame(1, TimeFrameUnit.Day),
    }


class AlpacaDataFetcher:
    """Fetch historical bars from Alpaca (IEX free tier) with local caching."""

    def __init__(self):
        self.client = _get_alpaca_client()

    def get_bars(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        timeframe: str = "5Min",
    ) -> pd.DataFrame | None:
        """Get OHLCV bars, using cache when possible.

        Args:
            symbol: Ticker symbol (e.g. "QQQ")
            start_date: ISO date string "YYYY-MM-DD"
            end_date: ISO date string "YYYY-MM-DD"
            timeframe: One of "1Min", "5Min", "15Min", "1Hour", "1Day"

        Returns:
            DataFrame with Title Case columns (Open, High, Low, Close, Volume)
            and datetime index, or None on failure.
        """
        # Try loading from cache first
        cached = load_cached_bars(symbol, timeframe, start_date, end_date)
        if cached:
            df = self._rows_to_df(cached)
            # Check if cache covers the full requested range
            cache_start = df.index[0].strftime("%Y-%m-%d")
            cache_end = df.index[-1].strftime("%Y-%m-%d")
            if cache_start <= start_date and cache_end >= end_date:
                logger.info(
                    "Cache hit for %s %s %s..%s (%d bars)",
                    symbol, timeframe, start_date, end_date, len(df),
                )
                return df

        # Fetch from Alpaca
        logger.info("Fetching %s %s %s..%s from Alpaca", symbol, timeframe, start_date, end_date)
        try:
            df = self._fetch_from_alpaca(symbol, start_date, end_date, timeframe)
        except Exception as e:
            logger.error("Alpaca fetch failed for %s: %s", symbol, e)
            # Return cached data if we have any, even if incomplete
            if cached:
                return self._rows_to_df(cached)
            return None

        if df is None or df.empty:
            logger.warning("No data returned for %s %s", symbol, timeframe)
            return None

        # Cache the fetched bars
        bars_to_cache = [
            {
                "timestamp": idx.isoformat(),
                "open": row["Open"],
                "high": row["High"],
                "low": row["Low"],
                "close": row["Close"],
                "volume": int(row["Volume"]),
            }
            for idx, row in df.iterrows()
        ]
        cache_bars(symbol, timeframe, bars_to_cache)
        logger.info("Cached %d bars for %s %s", len(bars_to_cache), symbol, timeframe)

        return df

    def _fetch_from_alpaca(
        self,
        symbol: str,
        start: str,
        end: str,
        timeframe: str = "5Min",
    ) -> pd.DataFrame:
        """Fetch bars from Alpaca IEX feed.

        The SDK handles pagination automatically.
        """
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf_map = _get_timeframe_map()
        tf = tf_map.get(timeframe, TimeFrame(5, TimeFrameUnit.Minute))

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=datetime.fromisoformat(start),
            end=datetime.fromisoformat(end),
            feed="iex",
        )

        # Rate limit safety
        time.sleep(0.5)

        bars = self.client.get_stock_bars(request)
        data = bars.get(symbol, [])

        if not data:
            return pd.DataFrame()

        records = []
        for bar in data:
            records.append({
                "timestamp": bar.timestamp,
                "Open": bar.open,
                "High": bar.high,
                "Low": bar.low,
                "Close": bar.close,
                "Volume": bar.volume,
            })

        df = pd.DataFrame(records)
        df.index = pd.to_datetime(df["timestamp"])
        df.index.name = None
        df.drop(columns=["timestamp"], inplace=True)
        df.sort_index(inplace=True)

        return df

    @staticmethod
    def _rows_to_df(rows: list[dict]) -> pd.DataFrame:
        """Convert cached bar rows to a DataFrame with Title Case columns."""
        df = pd.DataFrame(rows)
        df.rename(columns={
            "timestamp": "Timestamp",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "volume": "Volume",
        }, inplace=True)
        df.index = pd.to_datetime(df["Timestamp"])
        df.index.name = None
        df.drop(columns=["Timestamp"], inplace=True)
        df.sort_index(inplace=True)
        # Ensure numeric types
        for col in ["Open", "High", "Low", "Close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce").fillna(0).astype(int)
        return df
