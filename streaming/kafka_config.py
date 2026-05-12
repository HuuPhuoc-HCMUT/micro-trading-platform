"""Central Kafka connection configuration.

Reads environment variables and returns the appropriate kwargs for
:class:`kafka.KafkaConsumer` and :class:`kafka.KafkaProducer`.

Switch modes with a single variable in .env:

    KAFKA_ENV=development  →  local Docker broker  (kafka:29092, no SSL)
    KAFKA_ENV=production   →  Aiven cloud broker   (aivencloud.com, SSL)

Environment variables
---------------------
KAFKA_ENV                : development | production  (default: development)
KAFKA_BOOTSTRAP_SERVERS  : broker host:port  (only needed for production;
                           docker-compose injects kafka:29092 in dev mode)
KAFKA_SSL_CAFILE         : path to CA certificate PEM          (production)
KAFKA_SSL_CERTFILE       : path to client certificate PEM      (production)
KAFKA_SSL_KEYFILE        : path to client key PEM              (production)
"""

from __future__ import annotations

import os


def get_kafka_ssl_kwargs() -> dict:
    """Return SSL-related kwargs to spread into KafkaConsumer / KafkaProducer.

    Returns an empty dict for development mode so callers can always do
    ``**get_kafka_ssl_kwargs()`` unconditionally.
    """
    env = os.environ.get("KAFKA_ENV", "development").lower()

    # Development always uses PLAINTEXT — ignore any SSL vars that may be set.
    if env != "production":
        return {}

    return {
        "security_protocol": "SSL",
        "ssl_cafile": os.environ.get("KAFKA_SSL_CAFILE", "/app/certs/ca.pem"),
        "ssl_certfile": os.environ.get("KAFKA_SSL_CERTFILE", "/app/certs/service.cert"),
        "ssl_keyfile": os.environ.get("KAFKA_SSL_KEYFILE", "/app/certs/service.key"),
    }
