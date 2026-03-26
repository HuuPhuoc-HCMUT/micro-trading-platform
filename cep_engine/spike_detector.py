import logging
import math
from datetime import datetime, timezone
from statistics import fmean, pstdev

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class SpikeDetector:
    """Detects sudden price spikes or dumps over a short window."""

    def __init__(self, threshold_percent: float = 2.0, window_size: int = 5) -> None:
        self.threshold_percent = threshold_percent
        self.window_size = window_size
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
                confidence = min(0.99, 0.55 + abs(change_percent) / (self.threshold_percent * 4))

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
                    score=math.tanh(abs(change_percent) / self.threshold_percent) * (1 if change_percent > 0 else -1),
                    expected_return=change_percent / 100.0,
                    metadata={
                        "change_percent": round(change_percent, 6),
                        "shock_zscore": round(shock, 6),
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
