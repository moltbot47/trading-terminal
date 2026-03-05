"""Performance analytics computations for the trading terminal."""

import math
from collections import defaultdict
from datetime import datetime
from typing import Any


def compute_performance(
    scanner_hits: list[dict[str, Any]],
    broker_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute comprehensive performance metrics from trade data.

    Args:
        scanner_hits: Closed scanner hit records (status in won/lost/breakeven)
        broker_trades: Broker trade records with pnl field

    Returns:
        Dict with equity_curve, period breakdowns, per_strategy, and metrics.
    """
    # Normalize all trades to a common format: {timestamp, pnl, strategy_name}
    trades: list[dict[str, Any]] = []

    for h in scanner_hits:
        pnl = h.get("pnl_dollars") or h.get("pnl_points") or 0
        trades.append({
            "timestamp": h.get("exit_timestamp") or h.get("timestamp", ""),
            "pnl": float(pnl) if pnl else 0.0,
            "strategy_name": h.get("strategy_name", "Scanner"),
            "instrument": h.get("instrument", ""),
            "direction": h.get("direction", ""),
        })

    for b in broker_trades:
        pnl = b.get("pnl") or 0
        trades.append({
            "timestamp": b.get("timestamp", ""),
            "pnl": float(pnl) if pnl else 0.0,
            "strategy_name": "Broker",
            "instrument": b.get("instrument", ""),
            "direction": b.get("direction", ""),
        })

    # Sort by timestamp
    trades.sort(key=lambda t: t.get("timestamp", ""))

    if not trades:
        return _empty_result()

    # Equity curve
    equity_curve = _compute_equity_curve(trades)

    # Metrics
    metrics = _compute_metrics(trades, equity_curve)

    # Period breakdowns
    daily = _compute_period_breakdown(trades, "daily")
    weekly = _compute_period_breakdown(trades, "weekly")
    monthly = _compute_period_breakdown(trades, "monthly")

    # Per-strategy
    per_strategy = _compute_per_strategy(trades)

    return {
        "equity_curve": equity_curve,
        "daily_pnl": daily,
        "weekly_pnl": weekly,
        "monthly_pnl": monthly,
        "per_strategy": per_strategy,
        "metrics": metrics,
    }


def _empty_result() -> dict[str, Any]:
    """Return empty performance result."""
    return {
        "equity_curve": [],
        "daily_pnl": [],
        "weekly_pnl": [],
        "monthly_pnl": [],
        "per_strategy": [],
        "metrics": {
            "total_pnl": 0, "total_trades": 0, "win_rate": 0,
            "profit_factor": 0, "sharpe_ratio": 0, "max_drawdown": 0,
            "max_drawdown_pct": 0, "expectancy": 0, "avg_win": 0, "avg_loss": 0,
        },
    }


def _compute_equity_curve(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build cumulative equity curve from trades."""
    curve = []
    cumulative = 0.0
    for t in trades:
        cumulative += t["pnl"]
        ts = t.get("timestamp", "")
        # Convert to epoch seconds for Lightweight Charts
        epoch = _ts_to_epoch(ts)
        if epoch:
            curve.append({"time": epoch, "value": round(cumulative, 2)})
    return curve


def _compute_metrics(
    trades: list[dict[str, Any]],
    equity_curve: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute aggregate performance metrics."""
    pnls = [t["pnl"] for t in trades]
    total_trades = len(pnls)
    total_pnl = sum(pnls)

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_rate = len(wins) / total_trades if total_trades else 0
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
    expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss) if total_trades else 0

    # Max drawdown from equity curve
    max_dd, max_dd_pct = _compute_max_drawdown(equity_curve)

    # Sharpe ratio (daily returns → annualized)
    daily_returns = _group_pnl_by_day(trades)
    sharpe = _compute_sharpe(daily_returns)

    return {
        "total_pnl": round(total_pnl, 2),
        "total_trades": total_trades,
        "win_rate": round(win_rate, 4),
        "profit_factor": round(profit_factor, 2),
        "sharpe_ratio": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 4),
        "expectancy": round(expectancy, 2),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
    }


def _compute_max_drawdown(
    equity_curve: list[dict[str, Any]],
) -> tuple[float, float]:
    """Peak-to-trough drawdown from equity curve."""
    if not equity_curve:
        return 0.0, 0.0

    peak = equity_curve[0]["value"]
    max_dd = 0.0
    max_dd_pct = 0.0

    for point in equity_curve:
        val = point["value"]
        if val > peak:
            peak = val
        dd = peak - val
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = dd / peak if peak > 0 else 0.0

    return max_dd, max_dd_pct


def _compute_sharpe(daily_returns: list[float], risk_free: float = 0.0) -> float:
    """Annualized Sharpe ratio from daily P&L values."""
    if len(daily_returns) < 2:
        return 0.0

    mean_ret = sum(daily_returns) / len(daily_returns) - risk_free
    variance = sum((r - mean_ret - risk_free) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
    std_ret = math.sqrt(variance) if variance > 0 else 0.0

    if std_ret == 0:
        return 0.0

    return (mean_ret / std_ret) * math.sqrt(252)


def _group_pnl_by_day(trades: list[dict[str, Any]]) -> list[float]:
    """Group trades by calendar day and sum P&L."""
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        ts = t.get("timestamp", "")
        day = ts[:10] if len(ts) >= 10 else ts
        daily[day] += t["pnl"]
    return list(daily.values())


def _compute_period_breakdown(
    trades: list[dict[str, Any]], period: str,
) -> list[dict[str, Any]]:
    """Group trades by period and compute per-period stats."""
    groups: dict[str, list[float]] = defaultdict(list)

    for t in trades:
        ts = t.get("timestamp", "")
        key = _period_key(ts, period)
        groups[key].append(t["pnl"])

    result = []
    for key in sorted(groups.keys()):
        pnls = groups[key]
        wins = [p for p in pnls if p > 0]
        total = sum(pnls)
        result.append({
            "period": key,
            "pnl": round(total, 2),
            "trades": len(pnls),
            "wins": len(wins),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
        })

    return result


def _compute_per_strategy(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Break down performance by strategy."""
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        name = t.get("strategy_name", "Unknown")
        groups[name].append(t["pnl"])

    result = []
    for name in sorted(groups.keys()):
        pnls = groups[name]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total = sum(pnls)
        result.append({
            "strategy": name,
            "pnl": round(total, 2),
            "trades": len(pnls),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(pnls), 4) if pnls else 0,
            "avg_pnl": round(total / len(pnls), 2) if pnls else 0,
        })

    return result


def _period_key(timestamp: str, period: str) -> str:
    """Extract period key from ISO timestamp string."""
    if not timestamp or len(timestamp) < 10:
        return "unknown"

    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return timestamp[:10]

    if period == "daily":
        return dt.strftime("%Y-%m-%d")
    elif period == "weekly":
        return dt.strftime("%Y-W%W")
    elif period == "monthly":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def _ts_to_epoch(timestamp: str) -> int | None:
    """Convert ISO timestamp to UNIX epoch seconds."""
    if not timestamp:
        return None
    try:
        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return int(dt.timestamp())
    except (ValueError, TypeError):
        return None
