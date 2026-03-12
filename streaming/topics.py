"""Kafka topic name constants.

All topic names are centralised here so they can be imported by both
producers and consumers without magic strings scattered across the code.
"""

# Raw OHLCV candles / tick events from data sources
PRICE_EVENTS_TOPIC: str = "market.price_events"

# CEP signals emitted by the detector workers
ALERTS_TOPIC: str = "market.alerts"

# Paper-trade orders produced by the strategy worker
ORDERS_TOPIC: str = "market.orders"
