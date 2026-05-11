import logging
import math
from datetime import datetime, timezone
from statistics import fmean, pstdev

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class SpikeDetector:
    """Detects sudden price spikes or dumps over a short window."""

    def __init__(
        self,
        threshold_percent: float = 2.0,
        window_size: int = 5,
        transaction_cost_bps: float = 12.0,
        max_shock_zscore: float = 6.0,
    ) -> None:
        self.threshold_percent = threshold_percent
        self.window_size = window_size
        self.transaction_cost = transaction_cost_bps / 10_000.0
        self.max_shock_zscore = max_shock_zscore
        self.history: dict[str, list[float]] = {}
        self.return_history: dict[str, list[float]] = {}

    def process(self, event: PriceEvent) -> Alert | None:
        symbol = event.symbol

        # Initialize history for a new symbol
        if symbol not in self.history:
            self.history[symbol] = []
            self.return_history[symbol] = []

        self.history[symbol].append(event.price)

        # Keep only the number of values up to the window_size
        if len(self.history[symbol]) > self.window_size:
            self.history[symbol].pop(0)

        # Only calculate when we have gathered enough data points
        if len(self.history[symbol]) == self.window_size:
            oldest_price = self.history[symbol][0]
            current_price = event.price
            
            # Calculate percentage change
            change_percent = ((current_price - oldest_price) / oldest_price) * 100.0
            recent_returns = self.return_history[symbol]
            return_std = pstdev(recent_returns) if len(recent_returns) > 1 else 0.0
            average_return = fmean(recent_returns) if recent_returns else 0.0
            shock = ((change_percent / 100.0) - average_return) / return_std if return_std > 1e-9 else 0.0

            # Check if it exceeds the threshold (using absolute value to catch both directions)
            if abs(change_percent) >= self.threshold_percent:
                direction = "UP" if change_percent > 0 else "DOWN"
                trade_direction = "BUY" if change_percent > 0 else "SELL"
                signed_return = change_percent / 100.0
                shock_penalty = min(abs(shock) / self.max_shock_zscore, 0.85) if shock else 0.0
                continuation_quality = 1.0 - shock_penalty
                expected_return = math.copysign(
                    max(abs(signed_return) * 0.35 * continuation_quality - self.transaction_cost, 0.0),
                    signed_return,
                )

                if expected_return == 0.0:
                    return None

                confidence = min(
                    0.99,
                    0.52
                    + abs(change_percent) / (self.threshold_percent * 6)
                    + continuation_quality * 0.12,
                )
                score = (
                    math.tanh(abs(signed_return) / max(self.threshold_percent / 100.0, 1e-9))
                    * continuation_quality
                    * (1 if change_percent > 0 else -1)
                )

                return Alert(
                    signal_type="SPIKE_DETECTED",
                    symbol=symbol,
                    severity="HIGH",
                    message=(
                        f"Price spike {direction}: {change_percent:.2f}% "
                        f"over last {self.window_size} ticks. Current: ${current_price:.2f}"
                    ),
                    triggered_at=datetime.now(timezone.utc),
                    direction=trade_direction,
                    confidence=confidence,
                    score=score,
                    expected_return=expected_return,
                    metadata={
                        "change_percent": round(change_percent, 6),
                        "shock_zscore": round(shock, 6),
                        "continuation_quality": round(continuation_quality, 6),
                        "transaction_cost": round(self.transaction_cost, 6),
                        "window_size": self.window_size,
                    },
                )

        if len(self.history[symbol]) >= 2:
            previous_price = self.history[symbol][-2]
            if previous_price > 0:
                self.return_history[symbol].append((event.price - previous_price) / previous_price)
                if len(self.return_history[symbol]) > max(self.window_size * 4, 20):
                    self.return_history[symbol].pop(0)

        return None
