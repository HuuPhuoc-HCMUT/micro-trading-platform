# Kafka Integration — Technical Guide

> **Branch:** `feat/kafka`  
> **Author:** GitHub Copilot  
> **Date:** March 12, 2026  
> **Audience:** All team members (Tasks A / B / C / D)

---

## Table of Contents

1. [Why Kafka?](#1-why-kafka)
2. [High-Level Architecture](#2-high-level-architecture)
3. [New Files Overview](#3-new-files-overview)
4. [Code Logic — Deep Dive](#4-code-logic--deep-dive)
   - [4.1 `streaming/topics.py`](#41-streamingtopicspy)
   - [4.2 `streaming/serializers.py`](#42-streamingserializerspy)
   - [4.3 `streaming/kafka_producer.py`](#43-streamingkafka_producerpy)
   - [4.4 `streaming/kafka_consumer.py`](#44-streamingkafka_consumerpy)
   - [4.5 New modes in `main.py`](#45-new-modes-in-mainpy)
   - [4.6 `docker-compose.yml` — Infrastructure](#46-docker-composeyml--infrastructure)
5. [How Data Flows End-to-End](#5-how-data-flows-end-to-end)
6. [Running the Kafka Pipeline](#6-running-the-kafka-pipeline)
7. [Key Design Decisions](#7-key-design-decisions)
8. [Troubleshooting](#8-troubleshooting)
9. [What Stays Unchanged (Phase 1)](#9-what-stays-unchanged-phase-1)

---

## 1. Why Kafka?

The Phase 1 system uses **UDP sockets** to send data from the publisher to the subscriber.
UDP is fine for a local demo but has critical limitations in a real system:

| Problem | UDP | Kafka |
|---|---|---|
| Message lost if subscriber is down | ✅ Lost forever | ✅ Stored on broker, replayed on reconnect |
| Can only have one listener | ✅ One socket | ✅ Multiple consumer groups |
| No ordering guarantee | ✅ None | ✅ Per-partition ordering |
| Can replay historical events | ❌ No | ✅ Yes (configurable retention) |
| Monitor lag / throughput | ❌ No | ✅ Kafka UI at `localhost:8080` |

Kafka **decouples** the three processing stages so each one can crash, restart, or scale independently without affecting the others.

---

## 2. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PHASE 2 — KAFKA PIPELINE                 │
│                                                             │
│  Data Sources                                               │
│  (Simulator / Binance / Coinbase / Kraken)                  │
│          │                                                  │
│          ▼                                                  │
│  ┌───────────────────┐                                      │
│  │  kafka-publisher  │  TimeAggregator → 1-second candles   │
│  │  (main.py)        │  serialize → bytes                   │
│  └────────┬──────────┘                                      │
│           │  publishes PriceEvent JSON                      │
│           ▼                                                  │
│     Kafka Topic: market.price_events  (key = symbol)        │
│           │                                                  │
│           ▼                                                  │
│  ┌───────────────────┐                                      │
│  │  kafka-cep        │  MovingAvg + Spike + VolumeAnomaly   │
│  │  (main.py)        │  → Alert objects                     │
│  └────────┬──────────┘                                      │
│           │  publishes Alert JSON                           │
│           ▼                                                  │
│     Kafka Topic: market.alerts  (key = symbol)              │
│           │                                                  │
│           ▼                                                  │
│  ┌───────────────────┐                                      │
│  │  kafka-strategy   │  RuleBasedStrategy + OrderManager    │
│  │  (main.py)        │  → Order objects → SQLite            │
│  └────────┬──────────┘                                      │
│           │  publishes Order JSON                           │
│           ▼                                                  │
│     Kafka Topic: market.orders  (key = symbol)              │
│           │                                                  │
│           ▼                                                  │
│     API Server / DB Writer (future)                         │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. New Files Overview

```
streaming/
├── topics.py        ← Topic name constants (single source of truth)
├── serializers.py   ← Convert models ↔ JSON bytes
├── kafka_producer.py ← KafkaEventProducer class
└── kafka_consumer.py ← KafkaEventConsumer class

docker-compose.yml   ← Updated: added Zookeeper, Kafka, Kafka-UI
main.py              ← Updated: 3 new --mode options
```

---

## 4. Code Logic — Deep Dive

### 4.1 `streaming/topics.py`

```python
PRICE_EVENTS_TOPIC = "market.price_events"
ALERTS_TOPIC       = "market.alerts"
ORDERS_TOPIC       = "market.orders"
```

**Why it exists:** Instead of typing `"market.price_events"` as a string in 10 different places (and making a typo that is impossible to catch), every file imports the constant from here. One rename = one change.

**Rule:** Always import topic names from this file. Never hardcode a topic string anywhere else.

---

### 4.2 `streaming/serializers.py`

**Problem to solve:** Kafka messages are raw `bytes`. Our models are Pydantic objects. We need to convert between them cleanly.

**Pattern:** Each model type gets a pair of functions:

```
serialize_price_event(event: PriceEvent) → bytes    # Model → Kafka
deserialize_price_event(data: bytes) → PriceEvent   # Kafka → Model

serialize_alert(alert: Alert) → bytes
deserialize_alert(data: bytes) → Alert

serialize_order(order: Order) → bytes
deserialize_order(data: bytes) → Order
```

**How it works internally:**

```
PriceEvent object
      │
      ▼  (serialize)
Python dict  →  json.dumps()  →  .encode("utf-8")  →  bytes
      ▲
      │  (deserialize)
bytes  →  .decode("utf-8")  →  json.loads()  →  Python dict  →  PriceEvent(**dict)
```

The only tricky part is **datetime**: JSON has no datetime type, so we convert it to an ISO-8601 string (`"2026-03-12T10:00:00+00:00"`) on serialize and parse it back with `datetime.fromisoformat()` on deserialize.

**Why the producer/consumer don't do this themselves:** Keeping serialization separate means the producer and consumer classes work with any model type — they only handle transport.

---

### 4.3 `streaming/kafka_producer.py`

The `KafkaEventProducer` class is a thin wrapper around `kafka-python`'s `KafkaProducer`.

**Key design choices:**

#### Context Manager pattern
```python
with KafkaEventProducer() as producer:
    producer.send(PRICE_EVENTS_TOPIC, value=raw_bytes, key="BTC/USDT")
# → automatically flushes + closes on exit
```
This guarantees that pending messages in the internal buffer are always flushed to Kafka before the process exits, even if an exception is thrown.

#### Symbol as partition key
```python
producer.send(topic, value=raw_bytes, key="BTC/USDT")
```
Kafka partitions messages by key. Using the trading symbol as the key means **all BTC/USDT events always go to the same partition**, which guarantees they are consumed in the exact order they were produced. Without this, BTC events could arrive out of order.

#### Non-blocking error callback
```python
future = self._producer.send(...)
future.add_errback(_on_send_error, topic=topic, key=key)
```
`send()` is **asynchronous** — it puts the message in an internal buffer and returns immediately (very fast). If delivery fails after retries, `_on_send_error` logs the error without blocking the publisher loop.

#### Retries
```python
KafkaProducer(retries=3, ...)
```
If the broker is temporarily unreachable, kafka-python automatically retries 3 times before calling the error callback.

---

### 4.4 `streaming/kafka_consumer.py`

The `KafkaEventConsumer` class wraps `kafka-python`'s `KafkaConsumer`.

**Key design choices:**

#### Consumer Groups
```python
KafkaEventConsumer(topics=[PRICE_EVENTS_TOPIC], group_id="cep-worker")
```
A **consumer group** is a logical subscriber. Kafka tracks the last-read position (offset) per group. This means:
- If the CEP worker crashes and restarts, it picks up from where it left off — **no messages are lost**.
- You can run **two instances** of `kafka-cep` with the same `group_id` and Kafka will split the work between them (horizontal scaling).
- A completely separate consumer group (e.g., `"db-writer"`) can read the **same topic independently** from offset 0 without affecting the CEP worker.

#### `consume()` generator
```python
with consumer:
    for raw_bytes in consumer.consume():
        event = deserialize_price_event(raw_bytes)
        # process...
```
The `consume()` method is a Python generator (uses `yield`). It loops forever over Kafka messages and yields the raw bytes of each one. The caller's `for` loop looks identical to iterating a list — no manual poll loops needed.

#### `auto_offset_reset="latest"`
New consumers that have never connected before start reading **from now** (not from the beginning of the topic). Use `"earliest"` if you want to replay all historical messages from the retention window.

#### Graceful shutdown
```python
try:
    for message in self._consumer:
        yield message.value
except KeyboardInterrupt:
    logger.info("Consume loop interrupted by user.")
finally:
    self.close()  # always commits offset and closes
```
`Ctrl+C` is caught cleanly. The offset is committed to Kafka so the next startup resumes correctly.

---

### 4.5 New modes in `main.py`

Three new `--mode` values were added alongside the existing `publisher` and `subscriber`:

#### `kafka-publisher`
```
run_kafka_publisher(stream)
```
1. Creates a `TimeAggregator` (groups raw ticks into 1-second OHLCV candles — same logic as the UDP publisher).
2. For each closed candle, calls `serialize_price_event()` → sends bytes to `market.price_events` topic, keyed by symbol.
3. Reads broker address from `KAFKA_BOOTSTRAP_SERVERS` environment variable (defaults to `localhost:9092`).

#### `kafka-cep`
```
run_kafka_cep_worker()
```
1. Creates all 3 CEP detectors (same instances and thresholds as the UDP subscriber).
2. Subscribes to `market.price_events` as consumer group `"cep-worker"`.
3. For each message: deserializes → runs all 3 detectors → publishes any resulting `Alert` to `market.alerts`.
4. Errors from individual detectors are caught and logged — one bad message does not stop the worker.

#### `kafka-strategy`
```
run_kafka_strategy_worker()
```
1. Creates `OrderManager` and `RuleBasedStrategy` (same as before).
2. Subscribes to `market.alerts` as consumer group `"strategy-worker"`.
3. Maintains two in-memory data structures:
   - `price_cache`: `{symbol → latest_price}` — extracted from alert messages via regex
   - `alert_buffer`: `{symbol → [Alert, Alert, ...]}` — accumulates alerts between strategy calls
4. Each time an alert arrives for a symbol:
   - Try to extract the current price from the alert message text (e.g., `"spike UP ... @ $42000.00"`)
   - Accumulate the alert in the buffer
   - Build a lightweight synthetic `PriceEvent` from the cached price
   - Call `strategy.execute(alerts, synthetic_event)` — **exact same strategy logic as Phase 1**
   - If the latest order was `FILLED`, publish it to `market.orders`

**Why a synthetic PriceEvent?** The strategy was designed to receive `(alerts, event)` together. In the Kafka pipeline the price event arrives on a different topic than alerts, so we reconstruct the minimum required price info from the alert message itself rather than adding a cross-topic join.

---

### 4.6 `docker-compose.yml` — Infrastructure

Four services were added:

| Service | Image | Port | Purpose |
|---|---|---|---|
| `zookeeper` | confluentinc/cp-zookeeper:7.6.0 | 2181 | Kafka cluster coordinator (required by Kafka) |
| `kafka` | confluentinc/cp-kafka:7.6.0 | 9092 (host), 29092 (internal) | Message broker |
| `kafka-ui` | provectuslabs/kafka-ui | 8080 | Visual browser for topics and messages |
| `backend` | (existing) | 8000 | FastAPI server — now depends on Kafka health |

#### Two Kafka listener addresses — why?

```yaml
KAFKA_ADVERTISED_LISTENERS: PLAINTEXT://kafka:29092,PLAINTEXT_HOST://localhost:9092
```

- `kafka:29092` — used **inside Docker** (container-to-container communication). The `backend` container connects here.
- `localhost:9092` — used **from your laptop** (when you run `python main.py --mode kafka-publisher` directly without Docker).

If you ran Python workers via Docker Compose, change the address to `kafka:29092`.

#### Health checks
All services wait for their dependency to be healthy before starting:
```
zookeeper (healthy) → kafka (healthy) → kafka-ui, backend
```
This prevents the backend from crashing on startup because Kafka isn't ready yet.

---

## 5. How Data Flows End-to-End

Here is a single BTC candle flowing through the full pipeline:

```
1. [kafka-publisher]
   Binance WebSocket tick: {"p": "42200.50", "q": "0.015", "T": 1741776000123}
        ↓ normalizer.normalize()
   PriceEvent(symbol="BTC/USDT", price=42200.50, volume=0.015, ...)
        ↓ TimeAggregator.process()  (accumulates for 1 second)
   PriceEvent(open=42190, high=42210, low=42185, close=42200.50, volume=2.34, ticks_count=156)
        ↓ serialize_price_event()
   b'{"symbol":"BTC/USDT","price":42200.5,...}'
        ↓ KafkaEventProducer.send(topic="market.price_events", key="BTC/USDT")
   → Kafka partition 0 (BTC/USDT always goes to partition 0)

2. [kafka-cep]  (subscribed to market.price_events)
   receives raw bytes
        ↓ deserialize_price_event()
   PriceEvent(symbol="BTC/USDT", price=42200.50, ...)
        ↓ MovingAverageDetector.process(event)
   Alert(signal_type="MA_CROSSOVER", symbol="BTC/USDT", message="Golden cross @ $42200.50", ...)
        ↓ SpikeDetector.process(event)
   Alert(signal_type="SPIKE_DETECTED", symbol="BTC/USDT", message="spike UP +5.5% @ $42200.50", ...)
        ↓ VolumeAnomalyDetector.process(event)
   None  (volume normal)
        ↓ serialize_alert() × 2
   → Kafka topic "market.alerts"  (2 messages, both keyed "BTC/USDT")

3. [kafka-strategy]  (subscribed to market.alerts)
   receives Alert 1 (MA_CROSSOVER) → alert_buffer["BTC/USDT"] = [MA_CROSSOVER]
   receives Alert 2 (SPIKE_DETECTED) → alert_buffer["BTC/USDT"] = [MA_CROSSOVER, SPIKE_DETECTED]
   price extracted from message: 42200.50
        ↓ strategy.execute([MA_CROSSOVER, SPIKE_DETECTED], synthetic_event)
   signal_score = +1 (MA) + 1 (spike UP) = 2  → STRONG BUY
        ↓ order_manager.execute_order("BUY", "BTC/USDT", 0.075, 42200.50)
   Order(side="BUY", status="FILLED", ...)  → saved to SQLite
        ↓ serialize_order()
   → Kafka topic "market.orders"
```

---

## 6. Running the Kafka Pipeline

### Step 1 — Start Kafka (Docker)

```bash
docker compose up zookeeper kafka kafka-ui -d
```

Wait ~15 seconds for Kafka to be ready. Check at **http://localhost:8080** — you should see a broker listed.

### Step 2 — Install Python dependencies

Make sure `kafka-python` is installed:

```bash
pip install -r requirement.txt
```

### Step 3 — Open 3 terminals

**Terminal 1 — Publisher (data source → Kafka)**
```bash
# Simulated data
python main.py --mode kafka-publisher --source simulator

# Or real Binance data
python main.py --mode kafka-publisher --source binance --symbol BTC/USDT
```

**Terminal 2 — CEP Worker (Kafka → detector → Kafka)**
```bash
python main.py --mode kafka-cep
```

**Terminal 3 — Strategy Worker (Kafka → trading → Kafka + DB)**
```bash
python main.py --mode kafka-strategy
```

### Step 4 — Watch messages in Kafka UI

Open **http://localhost:8080**, go to **Topics**, and click on:
- `market.price_events` — see every 1-second candle
- `market.alerts` — see every CEP signal
- `market.orders` — see every paper trade

### Step 5 — Stop

`Ctrl+C` in each terminal. Workers commit their offsets before exiting, so on the next startup they resume from exactly where they left off.

---

## 7. Key Design Decisions

### Why is serialization separate from producer/consumer?

The `KafkaEventProducer` and `KafkaEventConsumer` accept and return raw `bytes`. They know nothing about `PriceEvent`, `Alert`, or `Order`. This means:

- You can test serializers independently (just call the function with a model, check the bytes)
- You can swap serialization format (e.g., to Avro or Protobuf) without touching the transport classes
- The transport classes can be reused for any future message type

### Why consumer group IDs matter

| Worker | `group_id` |
|---|---|
| CEP worker | `"cep-worker"` |
| Strategy worker | `"strategy-worker"` |

If you ever add a second consumer of `market.alerts` (e.g., a DB writer that logs all alerts), give it a **different** `group_id` (e.g., `"alert-db-writer"`). Kafka will let both groups read all messages independently.

If you run **two instances** of `kafka-cep` with the **same** `group_id="cep-worker"`, Kafka splits the partitions between them — automatic load balancing.

### Why `auto_offset_reset="latest"`?

When running in development you usually don't want to replay hours of old messages every time you restart a worker. `"latest"` means: start from now. Change to `"earliest"` only when you specifically want to replay history (e.g., backtesting).

### Why `KAFKA_BOOTSTRAP_SERVERS` env var?

```python
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
```

- **Running locally** (no Docker): default `localhost:9092` works.
- **Running in Docker Compose**: the `backend` service gets `KAFKA_BOOTSTRAP_SERVERS=kafka:29092` injected via `docker-compose.yml`.
- **Deploying to a cloud server**: just set the environment variable — no code change needed.

---

## 8. Troubleshooting

#### `NoBrokersAvailable` error on startup
Kafka isn't running or isn't ready yet.
```bash
docker compose ps   # check all services are "healthy"
docker compose logs kafka
```

#### Worker starts but receives no messages
Check that the publisher is running and producing to the correct topic. Open Kafka UI → Topics → `market.price_events` → Messages.

#### CEP worker receives messages but strategy worker gets nothing
The CEP worker is not producing alerts. Either the price window hasn't filled yet (Moving Average needs 60 candles before it can fire) or no patterns were detected. Watch the CEP worker logs.

#### `deserialize_price_event` raises `ValidationError`
The message format changed. This can happen if one teammate changed the `PriceEvent` model but didn't update `serializers.py`. Always update the serializer and re-deploy all workers together when changing a model.

#### Kafka UI shows high consumer lag
The consumer is falling behind the producer. Either speed up the consumer (add more instances with the same group_id) or reduce the publish rate (`--interval 2`).

---

## 9. What Stays Unchanged (Phase 1)

Everything from Phase 1 still works exactly as before:

```bash
# UDP mode — no Kafka needed
python main.py --mode publisher --source binance
python main.py --mode subscriber
```

The Kafka integration is **purely additive**. No existing code in `cep_engine/`, `trading_engine/`, `models/`, or `database/` was modified. The new `--mode kafka-*` options are simply new entry points that wire the same business logic to Kafka instead of UDP sockets.
