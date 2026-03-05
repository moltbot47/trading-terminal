"""Background scanner engine — evaluates strategies against live market data."""

import json
import logging
import threading
import time

import pandas as pd

from notifications.discord import DiscordNotifier
from strategy_lab import indicators as ind
from strategy_lab import models

logger = logging.getLogger(__name__)

# Minimum bars needed before scanning
MIN_BARS = 60

# Cooldown: don't re-trigger same strategy+instrument within N seconds
HIT_COOLDOWN_SECONDS = 600


class Scanner:
    """Background scanner that checks active strategies against price data."""

    def __init__(self, get_bars_fn, get_snapshot_fn, interval: float = 30.0):
        """
        Args:
            get_bars_fn: callable() -> dict[str, DataFrame|None]  (instrument -> OHLCV)
            get_snapshot_fn: callable() -> dict[str, PriceSnapshot]
            interval: seconds between scan cycles
        """
        self._get_bars = get_bars_fn
        self._get_snapshot = get_snapshot_fn
        self._interval = interval
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._last_hit: dict[str, float] = {}  # "strategy_id:instrument" -> timestamp
        self._discord = DiscordNotifier()

        # Lazy-init Alpaca trader (may not be configured)
        self._trader: object | None = None
        try:
            from execution.alpaca_trader import AlpacaPaperTrader
            trader = AlpacaPaperTrader()
            if trader.enabled:
                self._trader = trader
                logger.info("Alpaca auto-trade enabled")
        except Exception:
            pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="strategy-scanner")
        self._thread.start()
        logger.info("Strategy scanner started (interval=%ss)", self._interval)

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._scan_cycle()
            except Exception as e:
                logger.error("Scanner cycle error: %s", e)
            self._stop_event.wait(self._interval)

    def _scan_cycle(self) -> None:
        strategies = models.get_strategies(active_only=True)
        if not strategies:
            return

        bars = self._get_bars()
        snapshots = self._get_snapshot()
        now = time.time()

        # Also check and update active simulated trades
        self._update_active_trades(snapshots)

        for strat in strategies:
            strat_id = strat["id"]
            instruments = json.loads(strat["instruments"])
            entry_rules = json.loads(strat["entry_rules"])
            exit_rules = json.loads(strat["exit_rules"])
            direction_rules = json.loads(strat["direction_rules"])
            indicators_config = json.loads(strat["indicators_config"])

            if not entry_rules:
                continue

            for inst in instruments:
                df = bars.get(inst)
                if df is None or len(df) < MIN_BARS:
                    continue

                # Cooldown check
                cooldown_key = f"{strat_id}:{inst}"
                last = self._last_hit.get(cooldown_key, 0)
                if now - last < HIT_COOLDOWN_SECONDS:
                    continue

                models.increment_scan_count(strat_id, hit=False)

                # Compute indicators
                computed = ind.compute_indicators(df, indicators_config)

                # Check all entry conditions at latest bar
                all_met = True
                conditions_met = []
                for rule in entry_rules:
                    met = ind.evaluate_condition(rule, computed, df, idx=-1)
                    if met:
                        conditions_met.append(rule.get("indicator", "?"))
                    else:
                        all_met = False
                        break

                if not all_met:
                    continue

                # Determine direction
                direction = self._resolve_direction(direction_rules, computed, df)
                if not direction:
                    continue

                # Get entry price from snapshot
                snap = snapshots.get(inst)
                entry_price = snap.price if snap else float(df["Close"].iloc[-1])

                # Compute stop/target from exit rules
                stop_loss, take_profit = self._compute_exits(
                    exit_rules, direction, entry_price, computed, df
                )

                # Create the hit
                models.create_hit(
                    strategy_id=strat_id,
                    instrument=inst,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    confidence=len(conditions_met) / max(len(entry_rules), 1),
                    conditions_met=conditions_met,
                )
                self._last_hit[cooldown_key] = now
                logger.info(
                    "Scanner HIT: strategy=%s inst=%s dir=%s entry=%.2f sl=%.2f tp=%.2f",
                    strat["name"], inst, direction, entry_price,
                    stop_loss or 0, take_profit or 0,
                )

                # Discord notification (non-blocking daemon thread)
                if self._discord.enabled:
                    confidence = len(conditions_met) / max(len(entry_rules), 1)
                    threading.Thread(
                        target=self._discord.send_signal,
                        args=(
                            strat["name"], inst, direction, entry_price,
                            stop_loss, take_profit, confidence, conditions_met,
                        ),
                        daemon=True,
                        name="discord-notify",
                    ).start()

                # Auto-execute on Alpaca paper (non-blocking)
                if self._trader and self._trader.enabled:
                    threading.Thread(
                        target=self._auto_execute,
                        args=(inst, direction, entry_price, stop_loss, take_profit, strat["name"]),
                        daemon=True,
                        name="alpaca-execute",
                    ).start()

    def _auto_execute(
        self,
        instrument: str,
        direction: str,
        entry_price: float,
        stop_loss: float | None,
        take_profit: float | None,
        strategy_name: str,
    ) -> None:
        """Execute trade on Alpaca paper account (runs in daemon thread)."""
        try:
            result = self._trader.execute_signal(  # type: ignore[union-attr]
                instrument=instrument,
                direction=direction,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                strategy_name=strategy_name,
            )
            if result["success"]:
                logger.info(
                    "Auto-trade executed: %s %s %d shares, order=%s",
                    direction, instrument, result["qty"], result["order_id"],
                )
                # Send execution notification to Discord
                if self._discord.enabled:
                    self._discord.send_execution(
                        instrument, direction, result["qty"],
                        result["order_id"], strategy_name,
                    )
            else:
                logger.warning("Auto-trade skipped: %s — %s", instrument, result.get("error"))
        except Exception:
            logger.exception("Auto-trade error for %s", instrument)

    def _resolve_direction(
        self, rules: list[dict], indicators: dict, df: pd.DataFrame
    ) -> str | None:
        """Evaluate direction rules to determine long/short.

        If no direction rules, default to long.
        """
        if not rules:
            return "long"

        for rule in rules:
            met = ind.evaluate_condition(rule, indicators, df, idx=-1)
            if met:
                return rule.get("direction", "long")

        return None

    def _compute_exits(
        self,
        exit_rules: dict,
        direction: str,
        entry_price: float,
        indicators: dict,
        df: pd.DataFrame,
    ) -> tuple[float | None, float | None]:
        """Compute stop loss and take profit from exit rules."""
        stop_loss = None
        take_profit = None

        sl_config = exit_rules.get("stop_loss", {})
        tp_config = exit_rules.get("take_profit", {})

        # Stop loss
        method = sl_config.get("method", "")
        if method == "atr_multiple":
            atr_key = f"ATR_{sl_config.get('period', 14)}"
            atr_val = indicators.get(atr_key)
            if atr_val is not None:
                atr_now = float(atr_val.iloc[-1])
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

    def _update_active_trades(self, snapshots: dict) -> None:
        """Check active simulated trades for stop/target hits."""
        active = models.get_active_hits()
        for hit in active:
            inst = hit["instrument"]
            snap = snapshots.get(inst)
            if not snap:
                continue

            price = snap.price
            models.update_hit_tracking(hit["id"], price)

            sl = hit["stop_loss"]
            tp = hit["take_profit"]
            direction = hit["direction"]

            # Check stop loss
            if sl is not None and (
                (direction == "long" and price <= sl) or (direction == "short" and price >= sl)
            ):
                models.close_hit(hit["id"], sl, "stop_loss")
                continue

            # Check take profit
            if tp is not None and (
                (direction == "long" and price >= tp) or (direction == "short" and price <= tp)
            ):
                models.close_hit(hit["id"], tp, "take_profit")
                continue

            # Expire after 500 bars (~41 hours on 5m)
            if (hit["bars_held"] or 0) > 500:
                models.close_hit(hit["id"], price, "expired")
