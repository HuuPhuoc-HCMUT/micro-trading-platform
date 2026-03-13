"""Kafka producer wrapper.

Provides a thin, re-usable :class:`KafkaEventProducer` that handles:

- JSON serialisation (accepts raw ``bytes`` that callers produce via
  :mod:`streaming.serializers`)
- Symbol-based partition key so all events for the same trading pair land
  on the same partition (ordering guarantee)
- Delivery error logging
- Clean ``close()`` / context-manager support

Usage::

    from streaming.kafka_producer import KafkaEventProducer
    from streaming.serializers import serialize_price_event
    from streaming.topics import PRICE_EVENTS_TOPIC

    with KafkaEventProducer() as producer:
        raw = serialize_price_event(event)
        producer.send(PRICE_EVENTS_TOPIC, key=event.symbol, value=raw)
"""

from __future__ import annotations

import logging
from typing import Optional

from kafka import KafkaProducer
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"


class KafkaEventProducer:
    """Thin wrapper around :class:`kafka.KafkaProducer`.

    Serialisation of the *value* is deliberately left to the caller so this
    class stays model-agnostic.  The *key* (typically the trading symbol) is
    always UTF-8 encoded automatically.

    Args:
        bootstrap_servers: Comma-separated list of ``host:port`` broker
            addresses, or a list of strings.
        **kwargs: Additional keyword arguments forwarded verbatim to
            :class:`kafka.KafkaProducer`.
    """

    def __init__(
        self,
        bootstrap_servers: str | list[str] = DEFAULT_BOOTSTRAP_SERVERS,
        **kwargs,
    ) -> None:
        self._bootstrap_servers = bootstrap_servers
        self._producer: Optional[KafkaProducer] = None
        self._kwargs = kwargs

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the connection to the Kafka broker.

        Called automatically when used as a context manager.
        """
        logger.info("Connecting KafkaProducer to %s", self._bootstrap_servers)
        self._producer = KafkaProducer(
            bootstrap_servers=self._bootstrap_servers,
            # Value bytes are passed pre-serialised by the caller
            value_serializer=lambda v: v if isinstance(v, bytes) else v,
            # Keys are always plain strings → UTF-8 bytes
            key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
            # Retry on transient broker errors
            retries=3,
            **self._kwargs,
        )
        logger.info("KafkaProducer connected.")

    def close(self) -> None:
        """Flush pending messages and close the connection."""
        if self._producer is not None:
            self._producer.flush()
            self._producer.close()
            self._producer = None
            logger.info("KafkaProducer closed.")

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "KafkaEventProducer":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send(self, topic: str, value: bytes, key: str = "") -> None:
        """Publish a pre-serialised message to a Kafka topic.

        Args:
            topic: Target Kafka topic name (use constants from
                :mod:`streaming.topics`).
            value: Message payload as raw bytes (use a serialiser from
                :mod:`streaming.serializers`).
            key: Partition key — typically the trading symbol so all events
                for the same pair land on the same partition.  Defaults to
                an empty string (Kafka round-robins across partitions).

        Raises:
            RuntimeError: If :meth:`connect` has not been called yet.
        """
        if self._producer is None:
            raise RuntimeError(
                "KafkaEventProducer is not connected. "
                "Call connect() or use as a context manager."
            )

        future = self._producer.send(topic, value=value, key=key or None)

        # Attach async error callback — does NOT block the hot path
        future.add_errback(_on_send_error, topic=topic, key=key)

        logger.debug("Enqueued message on topic=%s key=%s", topic, key)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _on_send_error(exc: KafkaError, topic: str = "", key: str = "") -> None:
    """Callback invoked by kafka-python when a produce request fails."""
    logger.error(
        "Failed to deliver message to topic=%s key=%s: %s",
        topic, key, exc,
    )
