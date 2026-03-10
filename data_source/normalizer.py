import logging
from datetime import datetime, timezone

import requests

from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 10


def _parse_timestamp(ts: str | int | float | datetime | None) -> datetime:
    """Convert various timestamp formats to a UTC datetime."""
    if ts is None:
        return datetime.now(timezone.utc)
    if isinstance(ts, datetime):
        return ts
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
    if isinstance(ts, str):
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return datetime.now(timezone.utc)


def fetch_json(url: str, params: dict | None = None) -> dict:
    """GET a JSON endpoint with standard timeout and error handling."""
    response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return response.json()


def normalize(raw: dict) -> PriceEvent:
    """Convert raw exchange data into a standardized PriceEvent.

    Args:
        raw: Dict with keys: symbol, price, volume, timestamp, source.

    Returns:
        A validated PriceEvent.
    """
    event = PriceEvent(
        symbol=raw["symbol"],
        price=float(raw["price"]),
        volume=float(raw["volume"]),
        timestamp=_parse_timestamp(raw.get("timestamp")),
        source=raw.get("source", "unknown"),
    )

    logger.debug(
        "Normalized %s from %s: price=%.2f",
        event.symbol, event.source, event.price,
    )
    return event
