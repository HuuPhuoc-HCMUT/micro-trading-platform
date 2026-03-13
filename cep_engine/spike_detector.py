from __future__ import annotations

import logging
from datetime import datetime, timezone

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class SpikeDetector:
    """Detects sudden price spikes or dumps over a short window."""

    def __init__(self, threshold_percent: float = 2.0, window_size: int = 5) -> None:
        self.threshold_percent = threshold_percent
        self.window_size = window_size
        self.history: dict[str, list[float]] = {}

    def process(self, event: PriceEvent) -> Alert | None:
        symbol = event.symbol

        # Initialize history for a new symbol
        if symbol not in self.history:
            self.history[symbol] = []

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

            # Check if it exceeds the threshold (using absolute value to catch both directions)
            if abs(change_percent) >= self.threshold_percent:
                direction = "UP" if change_percent > 0 else "DOWN"
                
                # Clear history after an alert to prevent continuous spam for the same spike
                self.history[symbol].clear()
                
                return Alert(
                    signal_type="SPIKE_DETECTED",
                    symbol=symbol,
                    severity="HIGH",
                    message=(
                        f"Price spike {direction}: {change_percent:.2f}% "
                        f"over last {self.window_size} ticks. Current: ${current_price:.2f}"
                    ),
                    triggered_at=datetime.now(timezone.utc),
                )

        return None