from dataclasses import dataclass
from datetime import datetime, time
from math import floor


@dataclass(slots=True)
class MarketRules:
    """Execution constraints for cash-equity style markets."""

    lot_size: float = 0.0
    price_tick: float = 0.0
    price_band_percent: float = 0.0
    settlement_bars: int = 0
    enforce_trading_session: bool = False
    morning_start: time = time(9, 0)
    morning_end: time = time(11, 30)
    afternoon_start: time = time(13, 0)
    afternoon_end: time = time(14, 45)

    def normalize_quantity(self, quantity: float) -> float:
        quantity = max(quantity, 0.0)
        if self.lot_size <= 0:
            return quantity
        return floor(quantity / self.lot_size) * self.lot_size

    def normalize_price(self, price: float) -> float:
        if self.price_tick <= 0:
            return price
        return round(price / self.price_tick) * self.price_tick

    def is_price_inside_band(self, price: float, reference_price: float | None) -> bool:
        if self.price_band_percent <= 0 or reference_price is None or reference_price <= 0:
            return True
        lower = reference_price * (1.0 - self.price_band_percent / 100.0)
        upper = reference_price * (1.0 + self.price_band_percent / 100.0)
        return lower <= price <= upper

    def is_session_open(self, timestamp: datetime | None) -> bool:
        if not self.enforce_trading_session or timestamp is None:
            return True
        current = timestamp.time()
        in_morning = self.morning_start <= current <= self.morning_end
        in_afternoon = self.afternoon_start <= current <= self.afternoon_end
        return in_morning or in_afternoon
