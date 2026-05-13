# Micro Trading Platform: Event-Driven Architecture with Apache Kafka

## Kafka Configuration Statistics (Topics & Partitions)

To smoothly process 3 symbols (BTC, ETH, SOL) concurrently without bottlenecks or data race conditions, the Kafka cluster is initialized with the following configuration:

| Topic Name | Partitions | Routing Key | Purpose |
| :--- | :---: | :--- | :--- |
| `market.price_events` | **3** | `candle.symbol` | Ensures candles for the 3 coins travel on 3 separate lanes, maintaining strict chronological order. |
| `market.alerts` | **3** | `alert.symbol` | Enables load balancing of signals across 3 Trading Bots for parallel processing. |
| `market.orders` | **3** | `order.symbol` | Stores the final executed orders before safely writing them to the database. |

---

## Detailed Data Pipeline Description

The system operates on a **Unidirectional** model across 4 independent processing junctions:

### 1. Ingestion Node: UDP Multiplexer Publisher
* Opens a single UDP port (`127.0.0.1:9999`) acting as a funnel to collect raw tick data for all 3 coins (BTC, ETH, SOL) from the exchange.
* Aggregates the ticks into 1-second standard candles and publishes them to the `market.price_events` topic. It strictly assigns `key=candle.symbol`. Kafka hashes this key to automatically route BTC to Partition 0, ETH to Partition 1, and SOL to Partition 2.

### 2. Fan-out Junction: Market Price Processing
Data at the `market.price_events` topic is duplicated (fanned out) to 2 parallel services:
* **Service A (CEP Engine):** Consumes candles to calculate SMAs and Spikes. Thanks to partition routing, the historical price arrays for the 3 coins are strictly isolated. When a pattern is detected, it pushes an `Alert` to the `market.alerts` topic.
* **Service B (Live UI Streamer):** Consumes raw candles and uses WebSockets to push them directly to the user's browser interface (Real-time chart) bypassing the database entirely.

### 3. Load Balancing Junction: Parallel Order Matching (Consumer Group)
At the `market.alerts` topic, the system unleashes its distributed computing capabilities:
* Spin up 3 independent **Trading Strategy** processes, all sharing the exact same `group_id="strategy-group"`.
* Kafka automatically acts as a Load Balancer: It assigns Bot 1 to process Partition 0 (BTC only), Bot 2 to Partition 1 (ETH only), and Bot 3 to Partition 2 (SOL only). 
* The bots independently calculate Kelly-sizing, verify account balances, and execute trades. The results are published to the `market.orders` topic.

### 4. Post-processing Junction: Safe Data Persistence
* **Service C (Database Writer):** A single, dedicated worker running at the end of the pipeline. It quietly consumes executed orders from the `market.orders` topic and executes `INSERT` statements into the SQLite database.
* Because only exactly 1 worker process is permitted to touch the `.db` file, the system completely eradicates the `database is locked` concurrency risk typical of SQLite.

---

## Complete Architecture Diagram (ASCII Pipeline)

```text
[UDP Stream: BTC, ETH, SOL]
         │
         ▼
[Publisher Worker] --- (Assign key = symbol)
         │
         ▼
================= KAFKA TOPIC: market.price_events (3 Partitions) =================
         │                                       │
         ├─> (Branch 1)                          ├─> (Branch 2)
         ▼                                       ▼
  [CEP Engine]                           [Live UI Streamer] ---> [Web Browser]
 (Indicator Analysis)                   (Real-time Candle Push)
         │
         ▼
=================== KAFKA TOPIC: market.alerts (3 Partitions) =====================
         │                                                            
         ├──────────────────── Automatic Load Balancing ────────────────┐
         ▼                               ▼                              ▼
 [Strategy Bot 1]                [Strategy Bot 2]               [Strategy Bot 3]
(Process BTC Orders)            (Process ETH Orders)           (Process SOL Orders)
         │                               │                              │
         └───────────────────────────────┼──────────────────────────────┘
                                         ▼
=================== KAFKA TOPIC: market.orders (3 Partitions) =====================
                                         │
                                         ▼
                                 [Database Writer]
                     (Single-threaded - Safe write to SQLite)