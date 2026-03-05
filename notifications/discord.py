"""Discord webhook notifications for scanner signals."""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Rate limiting: max 5 messages per 10 seconds (Discord limit is 30/min)
_rate_lock = threading.Lock()
_rate_timestamps: list[float] = []
_RATE_WINDOW = 10.0
_RATE_MAX = 5


class DiscordNotifier:
    """Send formatted embeds to a Discord webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook_url = webhook_url or os.environ.get("DISCORD_WEBHOOK_URL", "")
        self._enabled = bool(self._webhook_url)

    @property
    def enabled(self) -> bool:
        """Whether Discord notifications are configured."""
        return self._enabled

    def send_signal(
        self,
        strategy_name: str,
        instrument: str,
        direction: str,
        entry_price: float,
        stop_loss: float | None,
        take_profit: float | None,
        confidence: float,
        conditions_met: list[str] | None = None,
    ) -> bool:
        """Send a scanner signal as a Discord embed. Returns True on success."""
        if not self._enabled:
            return False

        if not self._check_rate_limit():
            logger.warning("Discord rate limit exceeded, skipping notification")
            return False

        color = 0x16C60C if direction == "long" else 0xE74856
        arrow = "\u25b2" if direction == "long" else "\u25bc"

        fields: list[dict[str, Any]] = [
            {"name": "Direction", "value": f"{arrow} {direction.upper()}", "inline": True},
            {"name": "Entry", "value": f"${entry_price:,.2f}", "inline": True},
            {"name": "Confidence", "value": f"{confidence:.0%}", "inline": True},
        ]
        if stop_loss is not None:
            fields.append({"name": "Stop Loss", "value": f"${stop_loss:,.2f}", "inline": True})
        if take_profit is not None:
            fields.append({"name": "Take Profit", "value": f"${take_profit:,.2f}", "inline": True})
        if stop_loss and take_profit:
            risk = abs(entry_price - stop_loss)
            reward = abs(take_profit - entry_price)
            rr = reward / risk if risk > 0 else 0
            fields.append({"name": "R:R", "value": f"{rr:.1f}", "inline": True})
        if conditions_met:
            fields.append({"name": "Conditions", "value": ", ".join(conditions_met[:5]), "inline": False})

        embed: dict[str, Any] = {
            "title": f"\U0001f4e1 Scanner Signal: {instrument}",
            "description": f"Strategy: **{strategy_name}**",
            "color": color,
            "fields": fields,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Trading Terminal v3 Scanner"},
        }

        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        return self._post(payload)

    def send_execution(
        self,
        instrument: str,
        direction: str,
        qty: int,
        order_id: str,
        strategy_name: str = "",
    ) -> bool:
        """Send a trade execution notification."""
        if not self._enabled:
            return False

        if not self._check_rate_limit():
            return False

        arrow = "\u25b2" if direction == "long" else "\u25bc"
        color = 0x3B78FF  # blue for execution

        embed: dict[str, Any] = {
            "title": f"\u26a1 Trade Executed: {instrument}",
            "description": f"{arrow} {direction.upper()} {qty} shares",
            "color": color,
            "fields": [
                {"name": "Order ID", "value": order_id[:12], "inline": True},
                {"name": "Strategy", "value": strategy_name or "N/A", "inline": True},
            ],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "footer": {"text": "Trading Terminal v3 Auto-Trade"},
        }

        payload = json.dumps({"embeds": [embed]}).encode("utf-8")
        return self._post(payload)

    def _check_rate_limit(self) -> bool:
        """Check and update rate limit counter."""
        now = time.time()
        with _rate_lock:
            _rate_timestamps[:] = [t for t in _rate_timestamps if now - t < _RATE_WINDOW]
            if len(_rate_timestamps) >= _RATE_MAX:
                return False
            _rate_timestamps.append(now)
        return True

    def _post(self, payload: bytes) -> bool:
        """HTTP POST to webhook. Uses urllib to avoid adding requests dependency."""
        try:
            req = urllib.request.Request(
                self._webhook_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
                return resp.status in (200, 204)
        except Exception:
            logger.exception("Discord webhook failed")
            return False
