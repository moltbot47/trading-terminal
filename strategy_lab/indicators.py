"""Technical indicator calculations for the strategy scanner."""

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / (avg_loss + 1e-10)
    return 100 - (100 / (1 + rs))


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, pd.Series]:
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    macd_line = fast_ema - slow_ema
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return {"macd": macd_line, "signal": signal_line, "histogram": histogram}


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["High"], df["Low"], df["Close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low = df["High"], df["Low"]
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    atr_vals = atr(df, period)
    plus_di = 100 * ema(plus_dm, period) / (atr_vals + 1e-10)
    minus_di = 100 * ema(minus_dm, period) / (atr_vals + 1e-10)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
    return ema(dx, period)


def bollinger_bands(series: pd.Series, period: int = 20, std_dev: float = 2.0) -> dict[str, pd.Series]:
    mid = sma(series, period)
    std = series.rolling(window=period).std()
    return {"upper": mid + std_dev * std, "middle": mid, "lower": mid - std_dev * std}


def stochastic(df: pd.DataFrame, k_period: int = 14, d_period: int = 3) -> dict[str, pd.Series]:
    low_min = df["Low"].rolling(window=k_period).min()
    high_max = df["High"].rolling(window=k_period).max()
    k = 100 * (df["Close"] - low_min) / (high_max - low_min + 1e-10)
    d = sma(k, d_period)
    return {"k": k, "d": d}


def vwap(df: pd.DataFrame) -> pd.Series:
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    vol = df.get("Volume", pd.Series(1, index=df.index))
    cum_vol = vol.cumsum()
    cum_tp_vol = (typical * vol).cumsum()
    return cum_tp_vol / (cum_vol + 1e-10)


def compute_indicators(df: pd.DataFrame, configs: list[dict]) -> dict[str, pd.Series]:
    """Compute all indicators specified in configs.

    Each config: {"indicator": "EMA", "params": {"period": 9}}
    Returns dict keyed like "EMA_9", "RSI_14", etc.
    """
    results = {}
    close = df["Close"]

    for cfg in configs:
        ind = cfg["indicator"].upper()
        params = cfg.get("params", {})

        if ind == "EMA":
            p = params.get("period", 20)
            results[f"EMA_{p}"] = ema(close, p)
        elif ind == "SMA":
            p = params.get("period", 20)
            results[f"SMA_{p}"] = sma(close, p)
        elif ind == "RSI":
            p = params.get("period", 14)
            results[f"RSI_{p}"] = rsi(close, p)
        elif ind == "MACD":
            fast_p = params.get("fast", 12)
            slow_p = params.get("slow", 26)
            sig_p = params.get("signal", 9)
            m = macd(close, fast_p, slow_p, sig_p)
            results[f"MACD_{fast_p}_{slow_p}"] = m["macd"]
            results[f"MACD_SIGNAL_{sig_p}"] = m["signal"]
            results["MACD_HIST"] = m["histogram"]
        elif ind == "ADX":
            p = params.get("period", 14)
            results[f"ADX_{p}"] = adx(df, p)
        elif ind == "ATR":
            p = params.get("period", 14)
            results[f"ATR_{p}"] = atr(df, p)
        elif ind == "BBANDS" or ind == "BOLLINGER":
            p = params.get("period", 20)
            s = params.get("std_dev", 2.0)
            bb = bollinger_bands(close, p, s)
            results[f"BB_UPPER_{p}"] = bb["upper"]
            results[f"BB_MID_{p}"] = bb["middle"]
            results[f"BB_LOWER_{p}"] = bb["lower"]
        elif ind == "STOCHASTIC":
            kp = params.get("k_period", 14)
            dp = params.get("d_period", 3)
            stoch = stochastic(df, kp, dp)
            results[f"STOCH_K_{kp}"] = stoch["k"]
            results[f"STOCH_D_{dp}"] = stoch["d"]
        elif ind == "VWAP":
            results["VWAP"] = vwap(df)

    return results


def evaluate_condition(
    condition: dict,
    indicators: dict[str, pd.Series],
    df: pd.DataFrame,
    idx: int = -1,
) -> bool:
    """Evaluate a single entry/direction condition at bar index `idx`.

    Condition format:
        {"indicator": "RSI", "params": {"period": 14}, "condition": "<", "value": 70}
        {"indicator": "EMA", "params": {"period": 9}, "condition": "crosses_above",
         "reference": {"indicator": "EMA", "params": {"period": 21}}}
        {"indicator": "price", "condition": ">", "reference": {"indicator": "EMA", "params": {"period": 200}}}
    """
    ind_name = condition.get("indicator", "").upper()
    params = condition.get("params", {})
    cond_op = condition.get("condition", "")

    # Resolve left-hand value
    if ind_name == "PRICE" or ind_name == "CLOSE":
        lhs = df["Close"]
    elif ind_name == "HIGH":
        lhs = df["High"]
    elif ind_name == "LOW":
        lhs = df["Low"]
    elif ind_name == "VOLUME":
        lhs = df.get("Volume", pd.Series(0, index=df.index))
    else:
        period = params.get("period", 14)
        key = f"{ind_name}_{period}"
        # Special keys
        if ind_name == "MACD":
            fast = params.get("fast", 12)
            slow = params.get("slow", 26)
            key = f"MACD_{fast}_{slow}"
        elif ind_name == "MACD_SIGNAL":
            sig = params.get("signal", 9)
            key = f"MACD_SIGNAL_{sig}"
        elif ind_name == "MACD_HIST":
            key = "MACD_HIST"
        elif ind_name == "VWAP":
            key = "VWAP"

        lhs = indicators.get(key)
        if lhs is None:
            return False

    try:
        lhs_val = float(lhs.iloc[idx])
    except (IndexError, TypeError, ValueError):
        return False

    if np.isnan(lhs_val):
        return False

    # Static value comparison
    if "value" in condition:
        rhs_val = float(condition["value"])
        if cond_op == ">":
            return lhs_val > rhs_val
        elif cond_op == ">=":
            return lhs_val >= rhs_val
        elif cond_op == "<":
            return lhs_val < rhs_val
        elif cond_op == "<=":
            return lhs_val <= rhs_val
        elif cond_op == "==":
            return abs(lhs_val - rhs_val) < 1e-6
        return False

    # Reference indicator comparison
    if "reference" in condition:
        ref = condition["reference"]
        ref_name = ref.get("indicator", "").upper()
        ref_params = ref.get("params", {})

        if ref_name == "PRICE" or ref_name == "CLOSE":
            rhs = df["Close"]
        else:
            ref_period = ref_params.get("period", 14)
            ref_key = f"{ref_name}_{ref_period}"
            if ref_name == "VWAP":
                ref_key = "VWAP"
            rhs = indicators.get(ref_key)
            if rhs is None:
                return False

        try:
            rhs_val = float(rhs.iloc[idx])
            rhs_prev = float(rhs.iloc[idx - 1]) if abs(idx) < len(rhs) else rhs_val
            lhs_prev = float(lhs.iloc[idx - 1]) if abs(idx) < len(lhs) else lhs_val
        except (IndexError, TypeError, ValueError):
            return False

        if np.isnan(rhs_val):
            return False

        ops = {  # noqa: SIM116
            ">": lhs_val > rhs_val,
            "<": lhs_val < rhs_val,
            ">=": lhs_val >= rhs_val,
            "<=": lhs_val <= rhs_val,
            "crosses_above": lhs_prev <= rhs_prev and lhs_val > rhs_val,
            "crosses_below": lhs_prev >= rhs_prev and lhs_val < rhs_val,
        }
        return ops.get(cond_op, False)

    return False
