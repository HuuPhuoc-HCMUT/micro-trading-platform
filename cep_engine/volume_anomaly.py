import logging
from datetime import datetime, timezone

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class VolumeAnomalyDetector:
    """Detects abnormal trading volume spikes compared to a rolling average."""

    # Thêm min_volume = 0.5 (Chỉ quan tâm lệnh lớn hơn 0.5 BTC)
    def __init__(self, window_size: int = 10, multiplier: float = 3.0, min_volume: float = 0.5) -> None:
        self.window_size = window_size
        self.multiplier = multiplier
        self.min_volume = min_volume
        self.history: dict[str, list[float]] = {}

    def process(self, event: PriceEvent) -> Alert | None:
        symbol = event.symbol

        if symbol not in self.history:
            self.history[symbol] = []

        previous_volumes = self.history[symbol]

        if len(previous_volumes) == self.window_size:
            avg_volume = sum(previous_volumes) / self.window_size
            
            # LOGIC MỚI: Phải thỏa mãn hệ số nhân VÀ phải lớn hơn min_volume
            if avg_volume > 0 and event.volume >= (avg_volume * self.multiplier) and event.volume >= self.min_volume:
                
                alert = Alert(
                    signal_type="VOLUME_ANOMALY",
                    symbol=symbol,
                    severity="HIGH",
                    # Đổi %.2f thành %.4f để nhìn rõ số thập phân
                    message=(
                        f"Volume anomaly! Current: {event.volume:.4f} is "
                        f"{event.volume / avg_volume:.1f}x the average ({avg_volume:.4f})"
                    ),
                    triggered_at=datetime.now(timezone.utc),
                )
                
                self.history[symbol].append(event.volume)
                self.history[symbol].pop(0)
                
                return alert

        self.history[symbol].append(event.volume)
        if len(self.history[symbol]) > self.window_size:
            self.history[symbol].pop(0)

        return None