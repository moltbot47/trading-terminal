"""Alpaca paper trading execution engine.

Submits bracket orders (entry + stop loss + take profit) to Alpaca paper trading
when scanner signals fire. Includes position sizing, safety checks, and logging.
"""

import logging
import os
import threading
from typing import Any

logger = logging.getLogger(__name__)

_trading_client: Any = None
_trading_lock = threading.Lock()


def _get_trading_client() -> Any:
    """Lazy-init the Alpaca TradingClient for paper trading."""
    global _trading_client  # noqa: PLW0603
    if _trading_client is None:
        from alpaca.trading.client import TradingClient

        api_key = os.environ.get("ALPACA_API_KEY", "")
        secret_key = os.environ.get("ALPACA_SECRET_KEY", "")
        if not api_key or not secret_key:
            raise ValueError("ALPACA_API_KEY and ALPACA_SECRET_KEY required")
        _trading_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=True)
    return _trading_client


class AlpacaPaperTrader:
    """Execute trades on Alpaca paper account with risk management."""

    MAX_POSITIONS: int = 5
    MAX_RISK_PCT: float = 0.02  # 2% of equity per trade
    MAX_DRAWDOWN_PCT: float = 0.05  # 5% daily drawdown limit

    # Only stocks can be traded on Alpaca (not futures)
    TRADEABLE_SYMBOLS: set[str] = {"QQQ", "TQQQ", "SPY", "SPXL", "NVDA", "TSLA", "AMD"}

    def __init__(self) -> None:
        self._enabled = bool(os.environ.get("ALPACA_AUTO_TRADE", ""))

    @property
    def enabled(self) -> bool:
        """Whether auto-trading is enabled via env var."""
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def execute_signal(
        self,
        instrument: str,
        direction: str,
        entry_price: float,
        stop_loss: float | None,
        take_profit: float | None,
        strategy_name: str = "",
    ) -> dict[str, Any]:
        """Execute a scanner signal as an Alpaca paper trade.

        Returns dict with: success, order_id, qty, error
        """
        if not self._enabled:
            return {"success": False, "error": "Auto-trading disabled"}

        if instrument not in self.TRADEABLE_SYMBOLS:
            return {"success": False, "error": f"{instrument} not tradeable on Alpaca"}

        if stop_loss is None:
            return {"success": False, "error": "Stop loss required for position sizing"}

        # Safety checks
        safety = self._safety_checks(instrument)
        if not safety["passed"]:
            return {"success": False, "error": safety["reason"]}

        with _trading_lock:
            try:
                client = _get_trading_client()
                account = client.get_account()

                # Position sizing: risk 2% of equity per trade
                equity = float(account.equity)
                risk_per_share = abs(entry_price - stop_loss)
                if risk_per_share < 0.01:
                    return {"success": False, "error": "Stop loss too close to entry"}

                max_risk_dollars = equity * self.MAX_RISK_PCT
                qty = int(max_risk_dollars / risk_per_share)
                if qty < 1:
                    return {"success": False, "error": "Position size < 1 share"}

                # Cap at reasonable size
                qty = min(qty, 500)

                from alpaca.trading.enums import OrderClass, OrderSide, TimeInForce
                from alpaca.trading.requests import (
                    MarketOrderRequest,
                    StopLossRequest,
                    TakeProfitRequest,
                )

                side = OrderSide.BUY if direction == "long" else OrderSide.SELL

                order_data = MarketOrderRequest(
                    symbol=instrument,
                    qty=qty,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    order_class=OrderClass.BRACKET,
                    take_profit=TakeProfitRequest(limit_price=round(take_profit, 2)) if take_profit else None,
                    stop_loss=StopLossRequest(stop_price=round(stop_loss, 2)) if stop_loss else None,
                )

                order = client.submit_order(order_data)
                logger.info(
                    "Order submitted: %s %s %d x %s @ market (SL=%.2f TP=%.2f) strategy=%s",
                    direction, instrument, qty, order.id,
                    stop_loss or 0, take_profit or 0, strategy_name,
                )

                return {
                    "success": True,
                    "order_id": str(order.id),
                    "qty": qty,
                    "side": direction,
                    "symbol": instrument,
                }

            except Exception:
                logger.exception("Alpaca order failed for %s", instrument)
                return {"success": False, "error": "Order submission failed"}

    def _safety_checks(self, instrument: str) -> dict[str, Any]:
        """Pre-trade safety validation."""
        try:
            client = _get_trading_client()
            account = client.get_account()

            # Market must be open
            clock = client.get_clock()
            if not clock.is_open:
                return {"passed": False, "reason": "Market is closed"}

            # Max positions check
            positions = client.get_all_positions()
            if len(positions) >= self.MAX_POSITIONS:
                return {"passed": False, "reason": f"Max positions ({self.MAX_POSITIONS}) reached"}

            # Already holding this symbol?
            for pos in positions:
                if pos.symbol == instrument:
                    return {"passed": False, "reason": f"Already holding {instrument}"}

            # Daily drawdown check
            equity = float(account.equity)
            last_equity = float(account.last_equity)
            if last_equity > 0:
                daily_change = (equity - last_equity) / last_equity
                if daily_change < -self.MAX_DRAWDOWN_PCT:
                    return {"passed": False, "reason": f"Daily drawdown limit ({self.MAX_DRAWDOWN_PCT:.0%}) hit"}

            # Account active?
            if str(account.status) != "ACTIVE":
                return {"passed": False, "reason": f"Account status: {account.status}"}

            return {"passed": True, "reason": ""}

        except Exception as e:
            return {"passed": False, "reason": f"Safety check error: {e}"}

    def get_status(self) -> dict[str, Any]:
        """Return current trading status for the dashboard."""
        if not self._enabled:
            return {"enabled": False}
        try:
            client = _get_trading_client()
            account = client.get_account()
            positions = client.get_all_positions()
            pos_list = [
                {
                    "symbol": p.symbol,
                    "qty": str(p.qty),
                    "side": str(p.side),
                    "pnl": str(p.unrealized_pl),
                    "market_value": str(p.market_value),
                }
                for p in positions
            ]
            return {
                "enabled": True,
                "equity": float(account.equity),
                "buying_power": float(account.buying_power),
                "positions": pos_list,
                "max_positions": self.MAX_POSITIONS,
                "daily_pnl": float(account.equity) - float(account.last_equity),
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}
