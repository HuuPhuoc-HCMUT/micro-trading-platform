import random
import time
from datetime import datetime, timezone

from models.price_event import PriceEvent


def generate_price_stream(
    symbol: str = "BTC/USDT",
    start_price: float = 40000.0,
    interval: float = 1.0,
) -> PriceEvent:
    """Generate an infinite stream of simulated price events.

    Args:
        symbol: Trading pair symbol.
        start_price: Initial price to start the simulation.
        interval: Seconds between each tick.

    Yields:
        PriceEvent for each simulated tick.
    """
    current_price = start_price

    while True:
        change_percent = random.uniform(-0.1, 0.1)
        current_price *= 1 + change_percent

        event = PriceEvent(
            symbol=symbol,
            price=round(current_price, 2),
            volume=round(random.uniform(0.5, 10.0), 4),
            timestamp=datetime.now(timezone.utc),
            source="simulator",
        )

        yield event

        time.sleep(interval)