"""Serializers and deserializers for all domain models.

Every Kafka message is a UTF-8 encoded JSON payload.  The functions here
convert between Pydantic model instances and raw ``bytes`` so that the
producer/consumer wrappers stay model-agnostic.

Usage::

    raw  = serialize_price_event(event)        # bytes  → Kafka
    event = deserialize_price_event(raw)       # bytes ← Kafka

    raw   = serialize_alert(alert)
    alert = deserialize_alert(raw)

    raw   = serialize_order(order)
    order = deserialize_order(raw)
"""

from __future__ import annotations

import json
from datetime import datetime

from models.alert import Alert
from models.order import Order
from models.price_event import PriceEvent

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _to_bytes(obj: dict) -> bytes:
    """Encode a plain dict to UTF-8 JSON bytes."""
    return json.dumps(obj).encode("utf-8")


def _from_bytes(data: bytes) -> dict:
    """Decode UTF-8 JSON bytes to a plain dict."""
    return json.loads(data.decode("utf-8"))


def _dt_to_str(value: datetime | str) -> str:
    """Normalise a datetime (or already-string) to an ISO-8601 string."""
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _str_to_dt(value: str | datetime) -> datetime:
    """Parse an ISO-8601 string back to a datetime object."""
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


# ---------------------------------------------------------------------------
# PriceEvent
# ---------------------------------------------------------------------------

def serialize_price_event(event: PriceEvent) -> bytes:
    """Serialise a :class:`PriceEvent` to Kafka-ready bytes.

    Args:
        event: The price event to serialise.

    Returns:
        UTF-8 encoded JSON bytes.
    """
    payload = {
        "symbol": event.symbol,
        "price": event.price,
        "volume": event.volume,
        "timestamp": _dt_to_str(event.timestamp),
        "source": event.source,
        # Optional candle fields
        "open": event.open,
        "high": event.high,
        "low": event.low,
        "ticks_count": event.ticks_count,
    }
    return _to_bytes(payload)


def deserialize_price_event(data: bytes) -> PriceEvent:
    """Deserialise bytes from Kafka into a :class:`PriceEvent`.

    Args:
        data: UTF-8 encoded JSON bytes.

    Returns:
        A validated :class:`PriceEvent` instance.
    """
    payload = _from_bytes(data)
    payload["timestamp"] = _str_to_dt(payload["timestamp"])
    return PriceEvent(**payload)


# ---------------------------------------------------------------------------
# Alert
# ---------------------------------------------------------------------------

def serialize_alert(alert: Alert) -> bytes:
    """Serialise an :class:`Alert` to Kafka-ready bytes.

    Args:
        alert: The alert to serialise.

    Returns:
        UTF-8 encoded JSON bytes.
    """
    payload = {
        "signal_type": alert.signal_type,
        "symbol": alert.symbol,
        "severity": alert.severity,
        "message": alert.message,
        "triggered_at": _dt_to_str(alert.triggered_at),
    }
    return _to_bytes(payload)


def deserialize_alert(data: bytes) -> Alert:
    """Deserialise bytes from Kafka into an :class:`Alert`.

    Args:
        data: UTF-8 encoded JSON bytes.

    Returns:
        A validated :class:`Alert` instance.
    """
    payload = _from_bytes(data)
    payload["triggered_at"] = _str_to_dt(payload["triggered_at"])
    return Alert(**payload)


# ---------------------------------------------------------------------------
# Order
# ---------------------------------------------------------------------------

def serialize_order(order: Order) -> bytes:
    """Serialise an :class:`Order` to Kafka-ready bytes.

    Args:
        order: The order to serialise.

    Returns:
        UTF-8 encoded JSON bytes.
    """
    payload = {
        "side": order.side,
        "symbol": order.symbol,
        "quantity": order.quantity,
        "price": order.price,
        "status": order.status,
        "timestamp": _dt_to_str(order.timestamp),
    }
    return _to_bytes(payload)


def deserialize_order(data: bytes) -> Order:
    """Deserialise bytes from Kafka into an :class:`Order`.

    Args:
        data: UTF-8 encoded JSON bytes.

    Returns:
        A validated :class:`Order` instance.
    """
    payload = _from_bytes(data)
    payload["timestamp"] = _str_to_dt(payload["timestamp"])
    return Order(**payload)
