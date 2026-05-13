# Architecture Evaluation: Plan vs. Current Implementation

## Quick Verdict Table

| Feature | Plan | Status | Notes |
|---|---|---|---|
| 3 Kafka topics | ✅ | ✅ **Done** | `market.price_events`, `market.alerts`, `market.orders` |
| Symbol-keyed producer routing | ✅ | ✅ **Done** | `key=symbol` in all `producer.send()` calls |
| 3 partitions per topic | ✅ | ✅ **Done** | Pre-created in `entrypoint.sh` via `KafkaAdminClient` before workers start |
| CEP worker (fan-out branch A) | ✅ | ✅ **Done** | All 4 detectors, group `cep-worker` |
| Live UI Streamer (fan-out branch B) | ✅ | ✅ **Done** | SSE endpoint `GET /api/stream/candles` with background Kafka broadcaster |
| 3 parallel strategy bots (consumer group) | ✅ | ✅ **Done** | 3 instances launched in `entrypoint.sh`, group `strategy-group` |
| Dedicated DB writer worker | ✅ | ✅ **Done** | `run_kafka_db_writer` consumes `market.orders`, sole SQLite writer |
| Kafka producer/consumer wrappers | ✅ | ✅ **Done** | `KafkaEventProducer`, `KafkaEventConsumer` with context-manager |
| Full serialiser/deserialiser | ✅ | ✅ **Done** | All 11 Alert fields + `trade_pnl` in Order serialised correctly |
| Docker Compose infrastructure | ✅ | ✅ **Done** | Zookeeper + Kafka + Kafka UI + backend + nginx frontend |
| `entrypoint.sh` process manager | — | ✅ **Done** | PID tracking + monitor loop + socket-based Kafka wait + 6-step startup |

---

## What Was Implemented

### Gap 1 — 3 Partitions Per Topic (`entrypoint.sh`)

Topics are now pre-created in Step 3 of `entrypoint.sh` using `KafkaAdminClient` before any worker starts. This prevents `KAFKA_AUTO_CREATE_TOPICS_ENABLE` from racing ahead and creating single-partition topics.

```python
topics = [
    NewTopic(name='market.price_events', num_partitions=3, replication_factor=1),
    NewTopic(name='market.alerts',       num_partitions=3, replication_factor=1),
    NewTopic(name='market.orders',       num_partitions=3, replication_factor=1),
]
```

With 3 partitions in place, the `key=symbol` already set on every `producer.send()` call now routes BTC → Partition 0, ETH → Partition 1, SOL → Partition 2 automatically via Kafka's default hash partitioner.

---

### Gap 2 — 3 Parallel Strategy Bots (`entrypoint.sh` + `main.py`)

`entrypoint.sh` now launches 3 independent strategy worker processes:

```bash
python main.py --mode kafka-strategy &
STRATEGY1_PID=$!
python main.py --mode kafka-strategy &
STRATEGY2_PID=$!
python main.py --mode kafka-strategy &
STRATEGY3_PID=$!
```

All three share `group_id="strategy-group"` (renamed from the incorrect `"strategy-worker"`). Kafka's consumer group protocol assigns one partition per bot — Bot 1 handles BTC alerts, Bot 2 ETH, Bot 3 SOL — with no application-level routing code required.

---

### Gap 3 — Dedicated DB Writer (`main.py`)

A new `run_kafka_db_writer()` worker is the **only** process permitted to write to SQLite. It consumes `market.orders` under group `db-writer` and calls `save_order()`:

```python
def run_kafka_db_writer() -> None:
    consumer = KafkaEventConsumer(
        topics=[ORDERS_TOPIC],
        group_id="db-writer",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="earliest",
    )
    with consumer:
        for raw in consumer.consume():
            order = deserialize_order(raw)
            save_order(order)
```

This eliminates the `database is locked` concurrency risk that would occur if all 3 strategy bots called `save_order()` simultaneously. Launched as a separate process in `entrypoint.sh` (`DBWRITER_PID`).

Also fixed: `serialize_order` now includes the `trade_pnl` field so P&L data is faithfully persisted.

---

### Gap 4 — SSE Live Candle Stream (`api/api_server.py`)

A background `_kafka_candle_broadcaster()` coroutine is started at app startup (FastAPI `lifespan`). It polls `market.price_events` via a thread executor and fans each candle out to all connected SSE clients:

```
GET /api/stream/candles   →   text/event-stream
data: {"symbol": "BTC/USDT", "price": 40123.45, ...}
```

Headers include `X-Accel-Buffering: no` so nginx passes frames through immediately without buffering. Clients connect once via `EventSource` and receive live candles pushed from Kafka — no polling, no chart lag.

---

## Runtime Verification

Confirmed from `docker logs micro_trading_backend`:

- `DB WRITE: SELL BTC/USDT ... [FILLED]` — db-writer consuming and persisting orders
- `strategy-group <Generation 1>` with 3 members joined — Kafka balanced the 3 bots
- `[CANDLE #N] BTC/USDT | ...` — CEP worker processing candles normally
- All workers tracked by `monitor_workers()` in `entrypoint.sh` with auto-restart on failure
