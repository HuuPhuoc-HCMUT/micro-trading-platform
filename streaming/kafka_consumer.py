"""Kafka consumer wrapper.

Provides a thin, re-usable :class:`KafkaEventConsumer` that handles:

- Consumer group coordination (horizontal scaling)
- Automatic offset commit (at-least-once delivery)
- Graceful shutdown on ``KeyboardInterrupt``
- A simple ``consume()`` generator so callers just iterate over raw bytes

Usage::

    from streaming.kafka_consumer import KafkaEventConsumer
    from streaming.serializers import deserialize_price_event
    from streaming.topics import PRICE_EVENTS_TOPIC

    consumer = KafkaEventConsumer(
        topics=[PRICE_EVENTS_TOPIC],
        group_id="cep-worker",
    )
    with consumer:
        for raw in consumer.consume():
            event = deserialize_price_event(raw)
            # ... process event
"""

import logging
from collections.abc import Iterator
from typing import Optional

from kafka import KafkaConsumer
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)

DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"


class KafkaEventConsumer:
    """Thin wrapper around :class:`kafka.KafkaConsumer`.

    Deserialisation is left to the caller — ``consume()`` yields raw
    ``bytes`` values so this class stays model-agnostic.

    Args:
        topics: List of topic names to subscribe to.
        group_id: Consumer group identifier.  All instances sharing the
            same ``group_id`` will share the partition workload.
        bootstrap_servers: Comma-separated broker addresses.
        auto_offset_reset: Where to start reading when no committed offset
            exists.  ``"earliest"`` replays all retained messages;
            ``"latest"`` (default) skips historical data.
        **kwargs: Additional keyword arguments forwarded to
            :class:`kafka.KafkaConsumer`.
    """

    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str | list[str] = DEFAULT_BOOTSTRAP_SERVERS,
        auto_offset_reset: str = "latest",
        **kwargs,
    ) -> None:
        self._topics = topics
        self._group_id = group_id
        self._bootstrap_servers = bootstrap_servers
        self._auto_offset_reset = auto_offset_reset
        self._kwargs = kwargs
        self._consumer: Optional[KafkaConsumer] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Open the connection to the Kafka broker and subscribe to topics.

        Called automatically when used as a context manager.
        """
        logger.info(
            "Connecting KafkaConsumer group=%s to topics=%s on %s",
            self._group_id, self._topics, self._bootstrap_servers,
        )
        self._consumer = KafkaConsumer(
            *self._topics,
            bootstrap_servers=self._bootstrap_servers,
            group_id=self._group_id,
            auto_offset_reset=self._auto_offset_reset,
            # Values arrive as raw bytes; callers deserialise themselves
            value_deserializer=lambda v: v,
            # Keys arrive as bytes; decode to str for convenience
            key_deserializer=lambda k: k.decode("utf-8") if k else None,
            enable_auto_commit=True,
            auto_commit_interval_ms=1000,
            **self._kwargs,
        )
        logger.info("KafkaConsumer connected and subscribed.")

    def close(self) -> None:
        """Commit offsets and close the consumer."""
        if self._consumer is not None:
            self._consumer.commit()
            self._consumer.close()
            self._consumer = None
            logger.info("KafkaConsumer closed.")

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "KafkaEventConsumer":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def consume(self) -> Iterator[bytes]:
        """Yield raw message bytes indefinitely until interrupted.

        Iterates the subscribed topics and yields the raw ``value`` bytes of
        each message.  The caller is responsible for deserialising the bytes
        using the appropriate function from :mod:`streaming.serializers`.

        Gracefully stops (and closes the consumer) on ``KeyboardInterrupt``.

        Yields:
            Raw bytes payload of each Kafka message.

        Raises:
            RuntimeError: If :meth:`connect` has not been called yet.
            KafkaError: On unrecoverable broker errors.
        """
        if self._consumer is None:
            raise RuntimeError(
                "KafkaEventConsumer is not connected. "
                "Call connect() or use as a context manager."
            )

        logger.info(
            "Starting consume loop on topics=%s (group=%s)",
            self._topics, self._group_id,
        )

        try:
            for message in self._consumer:
                logger.debug(
                    "Received message topic=%s partition=%d offset=%d key=%s",
                    message.topic,
                    message.partition,
                    message.offset,
                    message.key,
                )
                yield message.value

        except KeyboardInterrupt:
            logger.info("Consume loop interrupted by user.")
        except KafkaError as exc:
            logger.error("Unrecoverable Kafka error in consume loop: %s", exc)
            raise
        finally:
            self.close()
