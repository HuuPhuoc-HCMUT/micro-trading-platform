from __future__ import annotations

import random
import time
from datetime import datetime, timezone
from typing import Iterator

from models.price_event import PriceEvent


def generate_price_stream(
    symbols_start_prices: dict[str, float] | None = None,
    interval: float = 0.2,
) -> Iterator[PriceEvent]:
    """Generate an infinite stream of simulated price events for multiple symbols."""
    
    # Use default starting prices if none are provided
    if symbols_start_prices is None:
        symbols_start_prices = {
            "BTC/USDT": 40000.0,
            "ETH/USDT": 2500.0,
            "SOL/USDT": 100.0
        }

    current_prices = symbols_start_prices.copy()

    while True:
        # Randomly select ONE symbol to update in this tick (simulating asynchronous data)
        symbol = random.choice(list(current_prices.keys()))

        # 5% chance of a sudden price spike
        if random.random() < 0.05:
            change_percent = random.uniform(-0.03, 0.03) # Spike up to +/- 3%
            volume_multiplier = random.uniform(5.0, 20.0) # Volume explodes by 5x to 20x
        else:
            change_percent = random.uniform(-0.002, 0.002) # Normal market fluctuation of +/- 0.2%
            volume_multiplier = 1.0

        # Calculate new price and volume
        current_prices[symbol] *= 1 + change_percent
        base_volume = random.uniform(0.5, 10.0)

        event = PriceEvent(
            symbol=symbol,
            price=round(current_prices[symbol], 2),
            volume=round(base_volume * volume_multiplier, 4),
            timestamp=datetime.now(timezone.utc),
            source="simulator",
        )

        yield event
        time.sleep(interval)