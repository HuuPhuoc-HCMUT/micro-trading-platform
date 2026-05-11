# Micro Trading Platform

> **Project Report — Cloud Computing Course**  
> Team of 4 · Ho Chi Minh City University of Technology (HCMUT) · 2026

A paper-trading system that simulates crypto trading using a **Complex Event Processing (CEP)** architecture. The pipeline reads real-time market prices, detects statistical patterns, makes automated buy/sell decisions, and persists all results to SQLite — no real money is ever used.

---

## Table of Contents

1. [System Architecture](#system-architecture)  
2. [Project Structure](#project-structure)  
3. [Core Components](#core-components)  
4. [Kafka Data Streaming](#kafka-data-streaming)  
5. [CEP Engine — Signal Detection](#cep-engine--signal-detection)  
6. [Trading Engine](#trading-engine)  
7. [How to Run](#how-to-run)  
8. [API Endpoints](#api-endpoints)  
9. [Web Dashboard](#web-dashboard)  
10. [Backtesting](#backtesting)  
11. [Database](#database)  
12. [Tech Stack](#tech-stack)  
13. [Task Assignments](#task-assignments)

---

## System Architecture

The pipeline flows in a single direction through five layers:

```
Data Source (Simulator / Binance / Coinbase / Kraken / Exness CSV)
        |  raw ticks (price + volume)
        v
   TimeAggregator  (data_source/time_aggregator.py)
        |  aggregates raw ticks -> 1-second OHLCV candles -> PriceEvent
        v
   CEP Engine  (cep_engine/)
   +- MovingAverageDetector      -- MA crossover (BUY / SELL)
   +- SpikeDetector              -- price spike > threshold %
   +- VolumeAnomalyDetector      -- abnormal volume x N
   +- ProbabilisticSignalDetector -- online learning, probability forecast
        |  Alert (signal_type, direction, score, confidence, expected_return)
        v
   Trading Engine  (trading_engine/)
   +- RuleBasedStrategy  -- aggregates alerts -> Kelly-sizing -> BUY / SELL / HOLD
   +- OrderManager       -- order lifecycle, positions, balance, P&L
        |  Order
        v
   Database (SQLite)  <-> FastAPI (api/api_server.py)
        |                         |
        +--- Dashboard (index.html, TradingView Lightweight Charts)
```

### Two Data Transport Modes

| Mode | Protocol | Description |
|------|----------|-------------|
| **Phase 1 - UDP** | UDP socket `127.0.0.1:9999` | Publisher sends candles -> Subscriber receives, runs CEP + Strategy |
| **Phase 2 - Kafka** | Apache Kafka `localhost:9092` | Three independent workers: kafka-publisher -> kafka-cep -> kafka-strategy |

---

## Project Structure

```
micro-trading-platform/
+-- main.py                         # Entry point -- all run modes
+-- run_bot.py                      # Auto-opens 2 terminals (publisher + subscriber)
+-- run_backtest.py                 # CLI for running backtest and parameter optimization
+-- backend.py                      # Entry point used by Docker container
+-- entrypoint.sh                   # Docker entrypoint script
+-- logger_config.py                # System-wide logging configuration
+-- requirement.txt
+-- docker-compose.yml              # Kafka + Zookeeper + Kafka UI + Backend + Frontend
+-- Dockerfile
|
+-- api/
|   +-- api_server.py               # FastAPI server + all REST endpoints
|
+-- backtesting/
|   +-- __init__.py
|   +-- engine.py                   # BacktestConfig, run_backtest(), optimize_backtest()
|
+-- cep_engine/
|   +-- moving_average.py           # MovingAverageDetector (short MA vs long MA)
|   +-- spike_detector.py           # SpikeDetector (% price change)
|   +-- volume_anomaly.py           # VolumeAnomalyDetector (volume x N)
|   +-- probabilistic_signal.py    # ProbabilisticSignalDetector (online learning)
|
+-- data_simulator/
|   +-- price_stream.py             # Generates synthetic price data (random walk)
|
+-- data_source/
|   +-- binance_client.py           # WebSocket stream from Binance
|   +-- coinbase_client.py          # WebSocket stream from Coinbase
|   +-- kraken_client.py            # WebSocket stream from Kraken
|   +-- exness_history.py           # Reads historical tick data from Exness CSV / ZIP
|   +-- normalizer.py               # Normalizes data from any source -> PriceEvent
|   +-- time_aggregator.py          # Aggregates raw ticks -> OHLCV candles
|
+-- database/
|   +-- db.py                       # init_db(), save_candle(), save_order(), save_alert()
|   +-- schema.sql
|
+-- models/
|   +-- price_event.py              # PriceEvent (symbol, price, volume, OHLC, ticks_count)
|   +-- alert.py                    # Alert (signal_type, direction, score, confidence, ...)
|   +-- order.py                    # Order (side, quantity, price, status, trade_pnl)
|
+-- streaming/
|   +-- kafka_producer.py           # KafkaEventProducer
|   +-- kafka_consumer.py           # KafkaEventConsumer
|   +-- serializers.py              # serialize / deserialize PriceEvent, Alert, Order
|   +-- topics.py                   # PRICE_EVENTS_TOPIC, ALERTS_TOPIC, ORDERS_TOPIC
|
+-- trading_engine/
|   +-- strategy.py                 # RuleBasedStrategy (Kelly-sizing, confluence logic)
|   +-- order_manager.py            # OrderManager (positions, balance, P&L)
|
+-- tests/                          # pytest unit tests
+-- docs/                           # Supplementary documentation
+-- index.html                      # Web dashboard (TradingView Lightweight Charts)
```

---

## Core Components

### 1. Data Sources

| Source | File | Description |
|--------|------|-------------|
| Simulator | `data_simulator/price_stream.py` | Generates random prices via geometric Brownian motion |
| Binance | `data_source/binance_client.py` | Real-time WebSocket price stream |
| Coinbase | `data_source/coinbase_client.py` | Real-time WebSocket price stream |
| Kraken | `data_source/kraken_client.py` | Real-time WebSocket price stream |
| Exness CSV | `data_source/exness_history.py` | Reads historical tick data from Exness CSV / ZIP files |

**TimeAggregator** (`data_source/time_aggregator.py`) accepts raw ticks from any source and aggregates them into OHLCV candles at a configurable interval (default: 1 second). Each closed candle is emitted as a `PriceEvent` with full `open, high, low, close, volume, ticks_count` fields.

---

## Kafka Data Streaming

Phase 2 replaces the UDP socket with **Apache Kafka** (Confluent Platform 7.6) as the inter-component message bus. This decouples the three processing stages — data ingestion, CEP detection, and trading strategy — into independent, horizontally-scalable workers that communicate only through Kafka topics.

### Topics

All topic names are centralised in `streaming/topics.py`:

| Topic | Produced by | Consumed by | Payload model |
|-------|-------------|-------------|---------------|
| `market.price_events` | `kafka-publisher` | `kafka-cep` | `PriceEvent` |
| `market.alerts` | `kafka-cep` | `kafka-strategy` | `Alert` |
| `market.orders` | `kafka-strategy` | (DB / dashboard) | `Order` |

### Message Flow

```
Exchange / Simulator
        |
        v
  kafka-publisher --[market.price_events]--> kafka-cep --[market.alerts]--> kafka-strategy
  (--mode kafka-publisher)                 (--mode kafka-cep)            (--mode kafka-strategy)
                                                                                   |
                                                                        [market.orders] --> SQLite
```

Each arrow is a Kafka topic. Workers can be scaled, restarted, or replaced independently without touching the others.

### Producer (`streaming/kafka_producer.py`)

`KafkaEventProducer` wraps `kafka-python`'s `KafkaProducer` with three design decisions:

1. **Value serialisation is delegated to the caller.** The producer accepts pre-serialised `bytes` from `streaming/serializers.py`, keeping the producer model-agnostic.
2. **Symbol-based partition key.** The trading symbol (e.g. `"BTC/USDT"`) is used as the Kafka message key. Kafka hashes the key to a partition, so all events for the same symbol land on the same partition. This guarantees **ordering** — the CEP detectors always receive candles in chronological order per symbol.
3. **Retry on transient errors.** The producer is configured with `retries=3` to handle momentary broker unavailability.

```python
# Example usage (from main.py run_kafka_publisher)
with KafkaEventProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS) as producer:
    raw = serialize_price_event(candle)
    producer.send(PRICE_EVENTS_TOPIC, key=candle.symbol, value=raw)
```

### Consumer (`streaming/kafka_consumer.py`)

`KafkaEventConsumer` wraps `KafkaConsumer` with:

1. **Consumer group coordination.** Each worker type uses a fixed `group_id` (`"cep-worker"`, `"strategy-worker"`). If multiple instances of the same worker are started, Kafka automatically distributes the partition workload between them — this is the foundation for horizontal scaling.
2. **At-least-once delivery.** Offsets are committed automatically after each batch. If a worker crashes mid-batch, messages will be redelivered on restart, ensuring no events are silently dropped.
3. **`auto_offset_reset="latest"` by default.** Workers joining a new consumer group start from the most recent messages rather than replaying the entire topic history. This avoids a flood of stale signals on restart.
4. **Generator-based API.** `consume()` is a plain Python generator that yields raw `bytes` values. Callers pipe them through `streaming/serializers.py` to reconstruct typed model objects.

```python
# Example usage (from main.py run_kafka_cep_worker)
consumer = KafkaEventConsumer(
    topics=[PRICE_EVENTS_TOPIC],
    group_id="cep-worker",
    bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
)
with consumer:
    for raw in consumer.consume():
        event = deserialize_price_event(raw)
        # ... run CEP detectors on event
```

### Serialization (`streaming/serializers.py`)

All models are serialized to/from JSON bytes using Pydantic's `.model_dump()`. `datetime` objects are converted to ISO 8601 strings on the way out and parsed back on the way in. Deserialization reconstructs fully-typed Pydantic models so the rest of the codebase never deals with raw dicts.

### Infrastructure (`docker-compose.yml`)

```
[Zookeeper :2181] ---> [Kafka broker :9092] ---> [Kafka UI :8080]
                                |
              +-----------------+------------------+
              v                                    v
    [backend (FastAPI) :8000]          [frontend (nginx) :80]
```

- **Zookeeper** manages Kafka cluster metadata and leader election.
- **Kafka broker** is reachable from inside the Docker network as `kafka:29092` and from the host as `localhost:9092` (configured via `KAFKA_ADVERTISED_LISTENERS`).
- **Kafka UI** provides a web interface at `http://localhost:8080` for inspecting topics, consumer group lag, and individual message payloads — invaluable for debugging during development.
- **`KAFKA_BOOTSTRAP_SERVERS`** is read from an environment variable, defaulting to `localhost:9092` for local runs and overridden to `kafka:29092` inside Docker containers.

### Why Kafka over UDP?

| Property | Phase 1 (UDP) | Phase 2 (Kafka) |
|----------|--------------|-----------------|
| Delivery guarantee | Best-effort (fire-and-forget) | At-least-once |
| Message persistence | None | Configurable retention (default 7 days) |
| Replay history | Not possible | Yes — by resetting consumer group offset |
| Horizontal scaling | Not possible | Yes — via consumer groups |
| Per-symbol ordering | Not guaranteed | Guaranteed (partition by symbol key) |
| Operational complexity | Minimal | Requires broker infrastructure |

---

## CEP Engine — Signal Detection

Complex Event Processing (CEP) is the practice of analysing streams of events to detect meaningful patterns in real time. In this system, the CEP engine sits between the data source and the trading engine. It receives closed OHLCV candles as `PriceEvent` objects and emits `Alert` objects whenever a statistically significant pattern is detected.

Each detector is **stateful per symbol** — it maintains its own price/volume history independently for each trading pair (`BTC/USDT`, `ETH/USDT`, `SOL/USDT`, etc.). All detectors are **decoupled from each other**: they run in parallel on the same event and each independently produces zero or one `Alert`. The trading engine downstream is responsible for combining multiple simultaneous alerts into a single decision.

The `Alert` model carries:

| Field | Type | Description |
|-------|------|-------------|
| `signal_type` | `str` | Which detector fired: `"MA_CROSSOVER"`, `"SPIKE_DETECTED"`, `"VOLUME_ANOMALY"`, `"PROBABILISTIC_SIGNAL"` |
| `direction` | `str` | Trade direction: `"BUY"` or `"SELL"` |
| `score` | `float` | Signed strength in `[-1, 1]` — positive = bullish, negative = bearish |
| `confidence` | `float` | Probability-like certainty in `[0, 1]` |
| `expected_return` | `float` | Estimated fractional return (e.g. `0.02` = 2%) |
| `severity` | `str` | `"LOW"`, `"MEDIUM"`, or `"HIGH"` |

### Detector 1 — Moving Average Crossover (`moving_average.py`)

**Concept:** The Simple Moving Average (SMA) smooths out price noise by averaging closing prices over a sliding window. Two SMAs with different window lengths — a fast (short) SMA and a slow (long) SMA — are tracked simultaneously. When the fast SMA crosses above the slow SMA, the market is considered to be entering an uptrend (**Golden Cross**). When it crosses below, the market is considered to be entering a downtrend (**Death Cross**).

**Parameters:**
- `short_window` (default: 15 candles) — fast SMA period
- `long_window` (default: 60 candles) — slow SMA period

**Algorithm:**
```
For each incoming PriceEvent:
  1. Append price to per-symbol price history
  2. Compute short_sma = mean(last short_window prices)
  3. Compute long_sma  = mean(last long_window prices)
  4. If both SMAs are available:
       If prev_short_sma <= prev_long_sma AND short_sma > long_sma:
           Golden Cross -> emit Alert(direction="BUY")
       If prev_short_sma >= prev_long_sma AND short_sma < long_sma:
           Death Cross  -> emit Alert(direction="SELL")
  5. Update prev_short_sma, prev_long_sma
```

The `score` is computed as `tanh((short_sma - long_sma) / long_sma * 100)`. The larger the gap between the two MAs, the stronger the signal. Confidence scales with MA separation normalized by recent price volatility (`pstdev`).

**Output:** `Alert(signal_type="MA_CROSSOVER", direction="BUY"|"SELL")`

---

### Detector 2 — Spike Detector (`spike_detector.py`)

**Concept:** Sudden large price movements indicate high-momentum events — news announcements, large market orders, or liquidity gaps. A spike is detected when the cumulative percentage price change across a short window of candles exceeds a configurable threshold.

**Parameters:**
- `threshold_percent` (default: 2.0%) — minimum % change to trigger
- `window_size` (default: 5 candles) — look-back window

**Algorithm:**
```
For each incoming PriceEvent:
  1. Append price to per-symbol history (sliding window of size window_size)
  2. When window is full:
       change_percent = (current_price - oldest_price) / oldest_price * 100
  3. If |change_percent| >= threshold_percent:
       direction = "BUY" if change_percent > 0 else "SELL"
       score = tanh(|change_percent| / threshold_percent) * sign(change_percent)
       confidence = min(0.99, 0.55 + |change_percent| / (threshold * 4))
       shock_zscore = (change_percent/100 - mean_return) / std_return
       -> emit Alert(signal_type="SPIKE_DETECTED", severity="HIGH")
```

The **shock z-score** contextualises the spike against the symbol's recent return distribution. A 2% move on a normally quiet instrument is far more significant than a 2% move on a highly volatile one.

**Output:** `Alert(signal_type="SPIKE_DETECTED", severity="HIGH", direction="BUY"|"SELL")`

---

### Detector 3 — Volume Anomaly Detector (`volume_anomaly.py`)

**Concept:** Abnormally high trading volume is a leading indicator that a large participant has entered the market, or that significant news has triggered a reaction. This detector compares the current candle's volume against a rolling average of recent volumes.

**Parameters:**
- `window_size` (default: 10 candles) — rolling average window
- `multiplier` (default: 3.0x) — how many times the rolling average the current volume must exceed

**Algorithm:**
```
For each incoming PriceEvent:
  1. Append volume to per-symbol volume history
  2. Compute rolling_avg_volume = mean(last window_size volumes)
  3. If current_volume > multiplier * rolling_avg_volume:
       score = tanh((current_volume / rolling_avg_volume - multiplier) / multiplier)
       -> emit Alert(signal_type="VOLUME_ANOMALY")
```

`VOLUME_ANOMALY` alerts do not specify a direction on their own. The trading strategy uses them as **confirmation boosters**: when a `VOLUME_ANOMALY` fires alongside a directional signal, the strategy multiplies the edge score by 1.15 and the expected return by 1.10.

**Output:** `Alert(signal_type="VOLUME_ANOMALY")`

---

### Detector 4 — Probabilistic Signal Detector (`probabilistic_signal.py`)

**Concept:** The three rule-based detectors fire on fixed thresholds. This detector is different: it learns the relationship between observable market features and future price direction using **online logistic regression** with stochastic gradient descent (SGD) and L2 regularization. It adapts continuously as new data arrives — no separate batch training phase is needed.

**Parameters:**
- `window_size` (default: 60 candles) — maximum history length per symbol
- `min_history` (default: 20 candles) — minimum candles before generating signals
- `learning_rate` (default: 0.08) — SGD step size
- `l2_penalty` (default: 0.002) — L2 regularization coefficient (prevents weight explosion)
- `signal_threshold` (default: 0.18) — minimum |predicted_probability - 0.5| to emit an alert

**Feature Vector (10 dimensions):**

| Index | Feature | Description |
|-------|---------|-------------|
| 0 | `short_momentum` | (price_t - price_{t-3}) / price_{t-3} |
| 1 | `medium_momentum` | (price_t - price_{t-8}) / price_{t-8} |
| 2 | `price_zscore` | (price_t - mean_price) / std_price |
| 3 | `volume_zscore` | (volume_t - mean_volume) / std_volume |
| 4 | `realized_volatility` | pstdev of recent log-returns |
| 5-9 | (extended features) | Additional momentum and volatility terms |

**Online Learning Loop:**
```
For each incoming PriceEvent at time t:
  1. Update per-symbol state: append price, volume, return
  2. Build feature vector x from current state (10 dimensions)
  3. ONLINE UPDATE (trains on the previous candle t-1 as a labelled sample):
       a. logit = dot(weights, prev_features)
       b. prediction = sigmoid(logit)
       c. label = 1 if price_t > price_{t-1} else 0   <- realized outcome
       d. error = label - prediction
       e. For each weight_i:
            weight_i += lr * (error * feature_i - l2 * weight_i)
  4. INFERENCE on current features:
       logit = dot(weights, x)
       prob = sigmoid(logit)
       deviation = |prob - 0.5|
  5. If deviation >= signal_threshold AND len(history) >= min_history:
       direction = "BUY" if prob > 0.5 else "SELL"
       confidence = min(0.95, deviation * 2)
       expected_return = (prob - 0.5) * realized_volatility * 2
       score = tanh(logit / 3)
       -> emit Alert(signal_type="PROBABILISTIC_SIGNAL")
```

The model learns and predicts in the same streaming pass — no offline training. The L2 penalty keeps weights bounded, preventing drift when the market regime changes. With `signal_threshold=0.18`, the detector only fires when the model has at least 68% directional confidence (0.5 + 0.18), reducing false positives during the warm-up period.

**Output:** `Alert(signal_type="PROBABILISTIC_SIGNAL", direction="BUY"|"SELL", confidence=..., expected_return=...)`

---

## Trading Engine

### RuleBasedStrategy (`strategy.py`)

Receives all `Alert` objects for a symbol from the current candle and executes a **6-step decision process**:

| Step | Description |
|------|-------------|
| A | **Signal aggregation** — compute weighted `edge`, `expected_return`, `confidence` from all alerts |
| B | **Volume confirmation boost** — if `VOLUME_ANOMALY` is present, multiply `edge x 1.15` and `expected_return x 1.10` |
| C | **BUY / SELL / HOLD decision** — requires `|edge| >= 0.2` AND `|expected_return| >= 0.05%` |
| D | **Win probability estimate** — `win_prob = 0.5 + min(|edge|, 1.5) / 3` |
| E | **Reward/risk ratio** — `R/R = |expected_return| / realized_volatility` (floor: 0.35) |
| F | **Kelly-sizing** — `kelly = win_prob - (1 - win_prob) / R_R`, scaled by `confidence`, capped at `max_balance_risk = 18%` |

Base trade quantities: `BTC/USDT = 0.05`, `ETH/USDT = 0.5`, `SOL/USDT = 5.0`.

### OrderManager (`order_manager.py`)
- Starting balance: configurable (default **$10,000 USDT**)
- Cannot buy if insufficient balance; cannot sell if no position is held
- Orders fill immediately at current market price (no order book simulation)
- Tracks: open positions, realized P&L, unrealized P&L against current price

---

## How to Run

### Requirements

```bash
pip install -r requirement.txt
```

Python 3.11+, Docker Desktop (for Phase 2 / full-stack).

### Phase 1 — UDP (no Kafka required)

**Option A: Automated script (Windows / macOS / Linux)**

```bash
python run_bot.py
```

Auto-opens 2 terminal windows: one for Subscriber, one for Publisher.

**Option B: Manual**

```bash
# Terminal 1 -- Subscriber (CEP + Strategy + DB)
python main.py --mode subscriber

# Terminal 2 -- Publisher (data source)
python main.py --mode publisher --source simulator --symbol BTC/USDT
python main.py --mode publisher --source binance   --symbol BTC/USDT
python main.py --mode publisher --source kraken    --symbol ETH/USD
```

---

### Phase 2 — Full Stack with Docker Compose (recommended)

Everything — Zookeeper, Kafka, Kafka UI, backend (all workers + API), and nginx frontend
— runs in containers with a single command.

```bash
# Start all services
docker compose up -d

# Custom data source / symbol (passed via environment variables)
SOURCE=binance SYMBOL=ETH/USDT docker compose up -d

# View live logs from all containers
docker compose logs -f

# Stop and remove containers
docker compose down
```

After `docker compose up -d`:

| URL | Service |
|-----|---------|
| `http://localhost` | Dashboard (nginx -> backend proxy) |
| `http://localhost:8080` | Kafka UI |

**How the containers are wired:**

```
[browser] -> nginx :80
                |-- GET /        -> serves index.html
                |-- GET /api/*   -> proxy -> backend:8000/api/*

[backend :8000]
    |-- uvicorn (FastAPI)
    |-- kafka-cep worker     (background)
    |-- kafka-strategy worker (background)
    |-- kafka-publisher       (background)
    +-- SQLite trading_platform.db

[kafka :9092 / kafka:29092]
    +-- zookeeper :2181
```

The `backend` container runs `entrypoint.sh`, which starts all three Kafka workers as
background processes and then launches uvicorn. The `frontend` nginx container proxies
`/api/*` requests to the backend so `window.location.origin` in `index.html` resolves
correctly — no hardcoded `localhost:8000` needed.

---

### Phase 2 — Kafka (local, workers outside Docker)

Use this when you want Kafka in Docker but run workers directly on your machine for
easier debugging.

**Step 1: Start only the Kafka infrastructure**

```bash
docker compose up -d zookeeper kafka kafka-ui
```

Wait ~10 seconds for the broker to become ready.

**Step 2 (Windows) — one-command launch with `run_kafka_all.ps1`**

```powershell
# Allow the script to run (only needed once per machine)
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

# Default: Binance BTC/USDT
.\run_kafka_all.ps1

# Custom source / symbol
.\run_kafka_all.ps1 -Source binance -Symbol ETH/USDT
.\run_kafka_all.ps1 -Source simulator -Symbol BTC/USDT
```

`run_kafka_all.ps1` runs the DB migration, checks Kafka is reachable, opens three
separate PowerShell windows (publisher, cep, strategy), then starts the API server
in the current window. Press **Ctrl+C** to stop the API server.

**Step 2 (Linux / macOS) — `entrypoint.sh`**

```bash
export SOURCE=binance SYMBOL=BTC/USDT
bash entrypoint.sh
```

**Step 2 (manual) — separate terminals**

```bash
python main.py --mode kafka-cep
python main.py --mode kafka-strategy
python main.py --mode kafka-publisher --source binance --symbol BTC/USDT
uvicorn api.api_server:app --host 0.0.0.0 --port 8000
```

### API Server + Dashboard

```bash
uvicorn api.api_server:app --reload --port 8000
```

Open browser: **http://localhost:8000**  
Kafka UI: **http://localhost:8080**

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/` | Serves `index.html` (dashboard) |
| `GET` | `/api/state` | Aggregated state: balance, positions, symbol list with action/edge/confidence |
| `GET` | `/api/focus?symbol=` | Detailed info for the focused symbol (price, trend, edge, confidence) |
| `POST` | `/api/focus?symbol=` | Set the symbol to focus on |
| `GET` | `/api/candles?symbol=&limit=` | OHLCV candle data (used by TradingView chart) |
| `GET` | `/api/orders?symbol=&limit=` | Order history, optionally filtered by symbol |
| `GET` | `/api/signals?limit=` | Recent CEP signal chain |
| `GET` | `/api/alerts?limit=` | Raw alert records from DB |
| `GET` | `/api/portfolio` | Latest balance and realized P&L |

---

## Web Dashboard

`index.html` is served directly via FastAPI at `GET /`.

**Features:**
- Interactive candlestick chart using **TradingView Lightweight Charts 4.1.1**
- Symbol grid: price, trend arrow (up/down/flat), action (BUY/SELL/HOLD), edge score
- Account stats: Balance, Equity, Realized/Unrealized PnL
- Real-time CEP signal feed (updates every second)
- Per-symbol order history
- "Updated: Xs ago" pill + manual refresh button
- Anti-flicker: symbol grid DOM only rebuilds when data actually changes
- Concurrent refresh guard

---

## Backtesting

```bash
# Single backtest (JSON output)
python run_backtest.py

# Parameter optimization -- returns top 3 best configurations
python run_backtest.py --optimize --top 3

# Filter by time range
python run_backtest.py --start "2026-01-01" --end "2026-03-31"
```

JSON output includes: `final_balance`, `total_return_pct`, `total_trades`, `win_rate`, `max_drawdown`, `sharpe_ratio`, `objective_score`.

`BacktestConfig` exposes all parameters of the 4 detectors and strategy. `optimize_backtest()` runs a grid search over the defined parameter space and returns results ranked by `objective_score` (P&L / drawdown).

---

## Database

SQLite file: `trading_platform.db` (auto-created on first `init_db()` call).

| Table | Key Columns |
|-------|-------------|
| `price_history` | `id`, `timestamp`, `symbol`, `open`, `high`, `low`, `close`, `volume`, `ticks_count` |
| `orders` | `id`, `timestamp`, `symbol`, `side`, `price`, `quantity`, `status`, `trade_pnl` |
| `balance_history` | `id`, `timestamp`, `balance_usdt`, `realized_pnl` |
| `alerts` | `id`, `triggered_at`, `signal_type`, `symbol`, `severity`, `message`, `price`, `direction`, `score`, `confidence` |

`db.py` handles automatic schema migrations (`ALTER TABLE`) for backward compatibility with older DB files.  
All queries use **parameterized statements** (`?` placeholders) — f-strings in SQL are never used.

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Web framework | FastAPI + Uvicorn |
| Data models | Pydantic v2 |
| Database | SQLite (`sqlite3` stdlib) |
| Message broker | Apache Kafka (Confluent Platform 7.6) |
| Charting | TradingView Lightweight Charts 4.1.1 |
| Testing | pytest |
| Linting | ruff |
| Logging | Python `logging` stdlib |
| Container | Docker + Docker Compose |
| Exchange clients | WebSocket (`websocket-client`) + REST (`requests`) |

---

## Task Assignments

| Task | Scope | Member |
|------|-------|--------|
| **A** | Data simulator + exchange clients (Binance, Coinbase, Kraken, Exness) + TimeAggregator | -- |
| **B** | CEP Engine: MovingAverage, SpikeDetector, VolumeAnomaly, ProbabilisticSignal | -- |
| **C** | Trading Engine: RuleBasedStrategy (Kelly-sizing), OrderManager, Backtesting | -- |
| **D** | Pipeline integration, Kafka streaming, FastAPI server, Dashboard, Docker | -- |

---

*This is a paper-trading system -- **no real money is used**. All orders are simulated.*