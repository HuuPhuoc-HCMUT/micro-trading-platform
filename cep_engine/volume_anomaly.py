import logging
import math
from datetime import datetime, timezone
from statistics import pstdev

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class VolumeAnomalyDetector:
    """Detects directional volume shocks compared to a rolling baseline."""

    def __init__(
        self,
        window_size: int = 10,
        multiplier: float = 3.0,
        min_volume: float = 0.5,
        min_price_move: float = 0.001,
        transaction_cost_bps: float = 12.0,
    ) -> None:
        self.window_size = window_size
        self.multiplier = multiplier
        self.min_volume = min_volume
        self.min_price_move = min_price_move
        self.transaction_cost = transaction_cost_bps / 10_000.0
        self.history: dict[str, list[float]] = {}
        self.price_history: dict[str, list[float]] = {}

    def process(self, event: PriceEvent) -> Alert | None:
        symbol = event.symbol

        if symbol not in self.history:
            self.history[symbol] = []
            self.price_history[symbol] = []

        previous_volumes = self.history[symbol]
        previous_prices = self.price_history[symbol]

        if len(previous_volumes) == self.window_size:
            avg_volume = sum(previous_volumes) / self.window_size
            volume_std = pstdev(previous_volumes) if len(previous_volumes) > 1 else 0.0
            zscore = (event.volume - avg_volume) / volume_std if volume_std > 1e-9 else 0.0

            if avg_volume > 0 and event.volume >= (avg_volume * self.multiplier) and event.volume >= self.min_volume:
                reference_price = previous_prices[-1] if previous_prices else event.price
                price_return = (
                    (event.price - reference_price) / reference_price
                    if reference_price > 0
                    else 0.0
                )
                signed_edge = 1.0 if price_return > 0 else -1.0
                expected_return = signed_edge * max(abs(price_return) - self.transaction_cost, 0.0)

                if abs(price_return) < self.min_price_move or expected_return == 0.0:
                    self.history[symbol].append(event.volume)
                    self.history[symbol].pop(0)
                    self.price_history[symbol].append(event.price)
                    if len(self.price_history[symbol]) > self.window_size:
                        self.price_history[symbol].pop(0)
                    return None

                volume_ratio = event.volume / avg_volume
                direction = "BUY" if price_return > 0 else "SELL"
                participation_score = math.tanh((volume_ratio - 1.0) / 2.0)
                price_confirmation = math.tanh(abs(price_return) / max(self.min_price_move * 3.0, 1e-9))
                
                alert = Alert(
                    signal_type="VOLUME_ANOMALY",
                    symbol=symbol,
                    severity="HIGH",
                    message=(
                        f"Directional volume anomaly {direction}: current={event.volume:.4f}, "
                        f"ratio={volume_ratio:.1f}x avg, price_move={price_return:.2%}"
                    ),
                    triggered_at=datetime.now(timezone.utc),
                    direction=direction,
                    confidence=min(0.98, 0.50 + min(volume_ratio, 6.0) / 16.0 + min(abs(zscore), 4.0) / 20.0),
                    score=signed_edge * participation_score * price_confirmation,
                    expected_return=expected_return,
                    metadata={
                        "avg_volume": round(avg_volume, 6),
                        "volume_ratio": round(volume_ratio, 6),
                        "volume_zscore": round(zscore, 6),
                        "price_return": round(price_return, 6),
                        "transaction_cost": round(self.transaction_cost, 6),
                    },
                )
                
                self.history[symbol].append(event.volume)
                self.history[symbol].pop(0)
                self.price_history[symbol].append(event.price)
                if len(self.price_history[symbol]) > self.window_size:
                    self.price_history[symbol].pop(0)
                
                return alert

        self.history[symbol].append(event.volume)
        if len(self.history[symbol]) > self.window_size:
            self.history[symbol].pop(0)
        self.price_history[symbol].append(event.price)
        if len(self.price_history[symbol]) > self.window_size:
            self.price_history[symbol].pop(0)

        return None
