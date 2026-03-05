"""Walk-forward backtester engine."""

import json
import logging
from datetime import datetime, timezone

import pandas as pd

from backtest import models
from strategy_lab.indicators import compute_indicators, evaluate_condition

logger = logging.getLogger(__name__)

# Minimum bars before we start evaluating signals
MIN_BARS = 60


class BacktestEngine:
    """Walk-forward backtest engine using strategy rules from Strategy Lab."""

    def __init__(self, strategy: dict, bars_df: pd.DataFrame):
        self.bars = bars_df
        self.strategy = strategy

        # Parse JSON fields (they may already be dicts/lists if coming from get_strategy)
        self.entry_rules = self._parse_json(strategy.get("entry_rules", "[]"))
        self.exit_rules = self._parse_json(strategy.get("exit_rules", "{}"))
        self.direction_rules = self._parse_json(strategy.get("direction_rules", "[]"))
        self.indicators_config = self._parse_json(strategy.get("indicators_config", "[]"))

    @staticmethod
    def _parse_json(val):
        if isinstance(val, str):
            return json.loads(val)
        return val

    def run(self, run_id: int) -> dict:
        """Execute the walk-forward backtest and save results to DB.

        Returns:
            dict with all stats, trades list, and equity_curve.
        """
        df = self.bars
        n = len(df)

        if n < MIN_BARS:
            models.update_run(
                run_id,
                status="failed",
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            return {"error": f"Not enough bars: {n} < {MIN_BARS}"}

        # Compute indicators once upfront
        indicators = compute_indicators(df, self.indicators_config)

        # Walk-forward state
        position = None  # {trade_id, direction, entry_price, entry_idx, sl, tp, mae, mfe}
        trades = []
        equity_curve = []
        cumulative_pnl = 0.0

        for i in range(MIN_BARS, n):
            bar_time = df.index[i]
            bar_close = float(df["Close"].iloc[i])
            bar_high = float(df["High"].iloc[i])
            bar_low = float(df["Low"].iloc[i])

            if position is None:
                # --- Check entry conditions ---
                all_met = True
                for rule in self.entry_rules:
                    if not evaluate_condition(rule, indicators, df, idx=i):
                        all_met = False
                        break

                if all_met and self.entry_rules:
                    direction = self._resolve_direction(indicators, df, i)
                    if direction:
                        entry_price = bar_close
                        sl, tp = self._compute_exits(direction, entry_price, indicators, df, i)

                        trade_id = models.create_trade(
                            run_id=run_id,
                            entry_time=str(bar_time),
                            direction=direction,
                            entry_price=entry_price,
                            stop_loss=sl,
                            take_profit=tp,
                        )
                        position = {
                            "trade_id": trade_id,
                            "direction": direction,
                            "entry_price": entry_price,
                            "entry_idx": i,
                            "sl": sl,
                            "tp": tp,
                            "mae": 0.0,
                            "mfe": 0.0,
                        }
            else:
                # --- Manage open position ---
                direction = position["direction"]
                entry_price = position["entry_price"]

                # Current excursion based on bar extremes
                if direction == "long":
                    favorable = bar_high - entry_price
                    adverse = entry_price - bar_low
                else:
                    favorable = entry_price - bar_low
                    adverse = bar_high - entry_price

                position["mfe"] = max(position["mfe"], favorable)
                position["mae"] = max(position["mae"], adverse)

                bars_held = i - position["entry_idx"]
                exit_price = None
                exit_reason = None

                sl = position["sl"]
                tp = position["tp"]

                # Check stop loss
                if sl is not None and ((direction == "long" and bar_low <= sl) or (direction == "short" and bar_high >= sl)):
                    exit_price = sl
                    exit_reason = "stop_loss"

                # Check take profit (only if SL not already hit)
                if exit_reason is None and tp is not None and ((direction == "long" and bar_high >= tp) or (direction == "short" and bar_low <= tp)):
                    exit_price = tp
                    exit_reason = "take_profit"

                # Expiry
                if exit_reason is None and bars_held >= 500:
                    exit_price = bar_close
                    exit_reason = "expired"

                # Close position if triggered
                if exit_reason is not None:
                    if direction == "long":
                        pnl = exit_price - entry_price
                    else:
                        pnl = entry_price - exit_price

                    models.close_trade(
                        trade_id=position["trade_id"],
                        exit_time=str(bar_time),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        pnl_points=pnl,
                        mae=position["mae"],
                        mfe=position["mfe"],
                        bars_held=bars_held,
                    )
                    trades.append({
                        "trade_id": position["trade_id"],
                        "entry_time": str(df.index[position["entry_idx"]]),
                        "exit_time": str(bar_time),
                        "direction": direction,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl_points": round(pnl, 4),
                        "exit_reason": exit_reason,
                        "mae_points": round(position["mae"], 4),
                        "mfe_points": round(position["mfe"], 4),
                        "bars_held": bars_held,
                    })
                    cumulative_pnl += pnl
                    position = None

            # Record equity curve point
            equity_curve.append({
                "time": int(bar_time.timestamp()) if hasattr(bar_time, "timestamp") else i,
                "value": round(cumulative_pnl, 4),
            })

        # --- Force close any open position at end of data ---
        if position is not None:
            bar_time = df.index[-1]
            bar_close = float(df["Close"].iloc[-1])
            direction = position["direction"]
            entry_price = position["entry_price"]
            bars_held = (n - 1) - position["entry_idx"]

            if direction == "long":
                pnl = bar_close - entry_price
            else:
                pnl = entry_price - bar_close

            models.close_trade(
                trade_id=position["trade_id"],
                exit_time=str(bar_time),
                exit_price=bar_close,
                exit_reason="end_of_data",
                pnl_points=pnl,
                mae=position["mae"],
                mfe=position["mfe"],
                bars_held=bars_held,
            )
            trades.append({
                "trade_id": position["trade_id"],
                "entry_time": str(df.index[position["entry_idx"]]),
                "exit_time": str(bar_time),
                "direction": direction,
                "entry_price": entry_price,
                "exit_price": bar_close,
                "pnl_points": round(pnl, 4),
                "exit_reason": "end_of_data",
                "mae_points": round(position["mae"], 4),
                "mfe_points": round(position["mfe"], 4),
                "bars_held": bars_held,
            })
            cumulative_pnl += pnl

        # --- Compute summary stats ---
        total_trades = len(trades)
        wins = sum(1 for t in trades if t["pnl_points"] > 0)
        losses = sum(1 for t in trades if t["pnl_points"] < 0)
        win_rate = round(wins / total_trades * 100, 2) if total_trades > 0 else 0.0
        total_pnl = round(sum(t["pnl_points"] for t in trades), 4)

        # Max drawdown from equity curve
        max_drawdown = 0.0
        peak = 0.0
        for pt in equity_curve:
            v = pt["value"]
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_drawdown:
                max_drawdown = dd
        max_drawdown = round(max_drawdown, 4)

        # Profit factor
        gross_win = sum(t["pnl_points"] for t in trades if t["pnl_points"] > 0)
        gross_loss = abs(sum(t["pnl_points"] for t in trades if t["pnl_points"] < 0))
        profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 0.0

        # Avg trades per day
        if len(df) > 1:
            total_days = (df.index[-1] - df.index[0]).total_seconds() / 86400
            avg_trades_per_day = round(total_trades / max(total_days, 1), 2)
        else:
            avg_trades_per_day = 0.0

        # Save results to DB
        now = datetime.now(timezone.utc).isoformat()
        models.update_run(
            run_id,
            status="completed",
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=total_pnl,
            max_drawdown=max_drawdown,
            profit_factor=profit_factor,
            avg_trades_per_day=avg_trades_per_day,
            equity_curve=equity_curve,
            completed_at=now,
        )

        results = {
            "run_id": run_id,
            "status": "completed",
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "max_drawdown": max_drawdown,
            "profit_factor": profit_factor,
            "avg_trades_per_day": avg_trades_per_day,
            "equity_curve": equity_curve,
            "trades": trades,
        }

        logger.info(
            "Backtest run %d completed: %d trades, %.2f%% win rate, %.2f PnL, %.2f PF",
            run_id, total_trades, win_rate, total_pnl, profit_factor,
        )

        return results

    def _resolve_direction(
        self, indicators: dict, df: pd.DataFrame, idx: int
    ) -> str | None:
        """Evaluate direction rules to determine long/short.

        If no direction rules, default to long.
        """
        if not self.direction_rules:
            return "long"

        for rule in self.direction_rules:
            met = evaluate_condition(rule, indicators, df, idx=idx)
            if met:
                return rule.get("direction", "long")

        return None

    def _compute_exits(
        self,
        direction: str,
        entry_price: float,
        indicators: dict,
        df: pd.DataFrame,
        idx: int,
    ) -> tuple[float | None, float | None]:
        """Compute stop loss and take profit from exit rules."""
        stop_loss = None
        take_profit = None

        sl_config = self.exit_rules.get("stop_loss", {})
        tp_config = self.exit_rules.get("take_profit", {})

        # Stop loss
        method = sl_config.get("method", "")
        if method == "atr_multiple":
            atr_key = f"ATR_{sl_config.get('period', 14)}"
            atr_val = indicators.get(atr_key)
            if atr_val is not None:
                atr_now = float(atr_val.iloc[idx])
                mult = sl_config.get("multiplier", 1.5)
                if direction == "long":
                    stop_loss = entry_price - (atr_now * mult)
                else:
                    stop_loss = entry_price + (atr_now * mult)
        elif method == "fixed_points":
            pts = sl_config.get("value", 20)
            if direction == "long":
                stop_loss = entry_price - pts
            else:
                stop_loss = entry_price + pts
        elif method == "fixed_percent":
            pct = sl_config.get("value", 1.0) / 100
            if direction == "long":
                stop_loss = entry_price * (1 - pct)
            else:
                stop_loss = entry_price * (1 + pct)

        # Take profit
        method = tp_config.get("method", "")
        if method == "risk_reward" and stop_loss is not None:
            ratio = tp_config.get("ratio", 2.0)
            risk = abs(entry_price - stop_loss)
            if direction == "long":
                take_profit = entry_price + (risk * ratio)
            else:
                take_profit = entry_price - (risk * ratio)
        elif method == "fixed_points":
            pts = tp_config.get("value", 40)
            if direction == "long":
                take_profit = entry_price + pts
            else:
                take_profit = entry_price - pts
        elif method == "fixed_percent":
            pct = tp_config.get("value", 2.0) / 100
            if direction == "long":
                take_profit = entry_price * (1 + pct)
            else:
                take_profit = entry_price * (1 - pct)

        return stop_loss, take_profit
