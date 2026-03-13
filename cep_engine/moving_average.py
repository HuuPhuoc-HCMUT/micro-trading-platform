from __future__ import annotations

import logging
from datetime import datetime, timezone

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)


class MovingAverageDetector:
    """Detects moving average crossover events (golden cross / death cross).

    Tracks short and long simple moving averages. When the short MA crosses
    above the long MA (golden cross), it signals a potential uptrend.
    When it crosses below (death cross), it signals a potential downtrend.

    Args:
        short_window: Number of periods for the short (fast) MA.
        long_window: Number of periods for the long (slow) MA.
    """

    def __init__(self, short_window: int = 5, long_window: int = 20) -> None:
        self.short_window = short_window
        self.long_window = long_window
        self.prices: dict[str, list[float]] = {}
        self.prev_short_ma: dict[str, float | None] = {}
        self.prev_long_ma: dict[str, float | None] = {}

    def _calculate_sma(self, values: list[float], window: int) -> float | None:
        """Return the simple moving average, or None if not enough data."""
        if len(values) < window:
            return None
        return sum(values[-window:]) / window

    def process(self, event: PriceEvent) -> Alert | None:
        """Process a price event and check for MA crossover.

        Args:
            event: The incoming price event.

        Returns:
            An Alert if a crossover is detected, otherwise None.
        """
        symbol = event.symbol

        # Initialize history for new symbols
        if symbol not in self.prices:
            self.prices[symbol] = []
            self.prev_short_ma[symbol] = None
            self.prev_long_ma[symbol] = None

        self.prices[symbol].append(event.price)

        # Keep only what we need (long_window is the max we ever look back)
        if len(self.prices[symbol]) > self.long_window * 2:
            self.prices[symbol] = self.prices[symbol][-self.long_window * 2:]

        short_ma = self._calculate_sma(self.prices[symbol], self.short_window)
        long_ma = self._calculate_sma(self.prices[symbol], self.long_window)

        if short_ma is None or long_ma is None:
            logger.debug(
                "Not enough data for %s (%d/%d prices)",
                symbol, len(self.prices[symbol]), self.long_window,
            )
            self.prev_short_ma[symbol] = short_ma
            self.prev_long_ma[symbol] = long_ma
            return None

        prev_short = self.prev_short_ma[symbol]
        prev_long = self.prev_long_ma[symbol]

        # Update state before returning
        self.prev_short_ma[symbol] = short_ma
        self.prev_long_ma[symbol] = long_ma

        # Need previous values to detect a *cross*
        if prev_short is None or prev_long is None:
            return None

        # Golden cross: short was below long, now short is above long
        if prev_short <= prev_long and short_ma > long_ma:
            
            return Alert(
                signal_type="MA_CROSSOVER",
                symbol=symbol,
                severity="MEDIUM",
                message=(
                    f"Golden cross: SMA({self.short_window})={short_ma:.2f} "
                    f"crossed above SMA({self.long_window})={long_ma:.2f}"
                ),
                triggered_at=datetime.now(timezone.utc),
            )

        # Death cross: short was above long, now short is below long
        if prev_short >= prev_long and short_ma < long_ma:
            
            return Alert(
                signal_type="MA_CROSSOVER",
                symbol=symbol,
                severity="MEDIUM",
                message=(
                    f"Death cross: SMA({self.short_window})={short_ma:.2f} "
                    f"crossed below SMA({self.long_window})={long_ma:.2f}"
                ),
                triggered_at=datetime.now(timezone.utc),
            )

        return None
