import logging
from datetime import datetime, timezone

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class VolumeAnomalyDetector:
    """Detects abnormal trading volume spikes compared to a rolling average."""

    def __init__(self, window_size: int = 10, multiplier: float = 3.0) -> None:
        """
        Args:
            window_size: Number of past events to calculate the average volume.
            multiplier: The threshold multiplier (e.g., 3.0 means 3x the average).
        """
        self.window_size = window_size
        self.multiplier = multiplier
        self.history: dict[str, list[float]] = {}

    def process(self, event: PriceEvent) -> Alert | None:
        symbol = event.symbol

        # Initialize history for a new symbol
        if symbol not in self.history:
            self.history[symbol] = []

        previous_volumes = self.history[symbol]

        # Only check for anomaly if we have enough historical data
        if len(previous_volumes) == self.window_size:
            avg_volume = sum(previous_volumes) / self.window_size
            
            # Check if current volume is strictly greater than Y * rolling_average
            if avg_volume > 0 and event.volume >= (avg_volume * self.multiplier):
                
                alert = Alert(
                    signal_type="VOLUME_ANOMALY",
                    symbol=symbol,
                    severity="HIGH",
                    message=(
                        f"Volume anomaly! Current: {event.volume:.2f} is "
                        f"{event.volume / avg_volume:.1f}x the average ({avg_volume:.2f})"
                    ),
                    triggered_at=datetime.now(timezone.utc),
                )
                
                # Update history before returning
                self.history[symbol].append(event.volume)
                self.history[symbol].pop(0)
                
                return alert

        # Update history with the new volume
        self.history[symbol].append(event.volume)
        if len(self.history[symbol]) > self.window_size:
            self.history[symbol].pop(0)

        return None