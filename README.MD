Data Simulator → CEP Engine → Trading Rule → Output

Mô phỏng data trước
CEP engine xử lý:
+ moving average
+ spike detection
+ anomaly detection

Sau khi pipeline chạy ổn, thay simulator bằng producer gửi vào Kafka.


Việc đầu tiên của cả nhóm (trong 1–2 ngày)
+ Tạo prototype chạy local:
+ price stream → CEP → trading rule → log output

Chưa cần: Kafka, API, UI (copy của Opensource Trading Platform) - TradingView Charting Library


Task:
+ A: tìm dataset crypto + viết market data simulator
+ B: CEP engine: implement các rule moving average, price spike detection, volume anomaly
+ C: Trading Engine: rule-based trading, order simulation, position tracking
+ D: nối pipeline, log output, chuẩn bị API



Tìm hiểu thêm phải có Rules của hệ thống nữa (ví dụ đòn bẩy, hết tiền không cho đặt, các chức năng thật của hệ thống stock trading ...)


keyword: CEP, Directly Access Memory

3) Come up with a program structure, data capture? data storage? data cleaning? processing? event processing? signal generation? order generation? order confirmation? real time p/l? accounting?

Here's the complete flow, step by step:

---

## The Big Picture

Your project is a **pipeline** — data flows in one direction through 5 stages:

```
Raw Price Data → Normalize → Detect Patterns → Make Trading Decisions → Store & Expose
```

---

## Stage 1: Data Generation — price_stream.py

**What happens:** The simulator creates fake but realistic crypto price ticks (BTC/USDT, ETH/USDT, etc.). Each tick contains: symbol, price, volume, and timestamp.

**How it works:**
1. Start with a base price (e.g., BTC at $40,000)
2. Every iteration (say every 1 second), generate a new price by adding small random changes (random walk / geometric Brownian motion)
3. Generate a random volume too
4. Output: a raw dict or tuple like `{"symbol": "BTC/USDT", "price": 40123.45, "volume": 2.5, "timestamp": "2026-03-10T12:00:01"}`

**In Phase 2**, this gets replaced by real exchange clients ([binance_client.py](data_source/binance_client.py), [coinbase.client.py](data_source/coinbase.client.py), [kraken_client.py](data_source/kraken_client.py)) that pull live prices via WebSocket/REST APIs. But for Phase 1, the simulator is all you need.

---

## Stage 2: Normalization — `data_source/normalizer.py`

**What happens:** Raw data (whether from the simulator or a real exchange) gets converted into a **standardized** `PriceEvent` Pydantic model.

**Why this exists:** Different sources return data in different formats:
- Binance might say `{"s": "BTCUSDT", "p": "40123.45"}`
- Coinbase might say `{"product_id": "BTC-USD", "price": "40123.45"}`
- Your simulator might say `{"symbol": "BTC/USDT", "price": 40123.45}`

The normalizer translates all of these into one consistent format:

```python
PriceEvent(
    symbol="BTC/USDT",
    price=40123.45,
    volume=2.5,
    timestamp=datetime(2026, 3, 10, 12, 0, 1),
    source="simulator"
)
```

**This is the contract** between Stage 1 and the rest of the system. Everything downstream only speaks `PriceEvent`.

---

## Stage 3: CEP Engine — `cep_engine/`

**What happens:** The CEP (Complex Event Processing) engine receives a stream of `PriceEvent`s and looks for **patterns** — specific conditions that might signal a trading opportunity. Each detector maintains a rolling window of recent events.

### 3a. Moving Average Crossover — [moving_average.py](cep_engine/moving_average.py)

**Logic:**
1. Maintain two moving averages: **short** (e.g., 5 periods) and **long** (e.g., 20 periods)
2. Short MA = average of the last 5 prices, Long MA = average of the last 20
3. **Golden Cross** (BUY signal): short MA was *below* long MA, now crosses *above* → price trending up
4. **Death Cross** (SELL signal): short MA was *above* long MA, now crosses *below* → price trending down

```
Price: ~~~~~/\/\/\~~~~~
Short MA:   ──╱──        (reacts fast)
Long MA:    ───────      (reacts slow)
                ↑ cross = signal!
```

**Output:** `Alert(signal_type="MA_CROSSOVER", severity="MEDIUM", ...)`  or  `None`

### 3b. Spike Detector — [spike_detector.py](cep_engine/spike_detector.py)

**Logic:**
1. Compare the latest price to a recent price (e.g., N seconds ago)
2. Calculate percentage change: $(price_{now} - price_{prev}) / price_{prev}$
3. If $|change| > threshold$ (e.g., 5%), fire an alert

**Example:** BTC was $40,000 → now $42,500 in 10 seconds = +6.25% → **SPIKE_DETECTED**

**Output:** `Alert(signal_type="SPIKE_DETECTED", severity="HIGH", ...)`  or  `None`

### 3c. Volume Anomaly — `cep_engine/volume_anomaly.py`

**Logic:**
1. Track a rolling average of volume over the last N periods
2. If current volume > $Y \times rolling\_average$ (e.g., 3x), flag it
3. Abnormal volume often precedes big price moves

**Example:** Average volume is 100 BTC/min → current volume is 450 BTC/min → 4.5x → **VOLUME_ANOMALY**

**Output:** `Alert(signal_type="VOLUME_ANOMALY", severity="HIGH", ...)`  or  `None`

---

## Stage 4: Trading Engine — `trading_engine/`

### 4a. Strategy — [strategy.py](trading_engine/strategy.py)

**What happens:** Receives `Alert`s from the CEP engine and makes **BUY/SELL/HOLD decisions** based on rules.

**Example rules:**
| Alert | Action |
|-------|--------|
| MA_CROSSOVER (golden cross) | BUY |
| MA_CROSSOVER (death cross) | SELL |
| SPIKE_DETECTED (price up) + VOLUME_ANOMALY | BUY (strong signal) |
| SPIKE_DETECTED (price down) | SELL |
| No alerts | HOLD (do nothing) |

The strategy can also combine multiple alerts — e.g., only buy when *both* MA crossover and volume anomaly fire together (higher confidence).

**Output:** A decision like `("BUY", "BTC/USDT", quantity)` or `None` (hold).

### 4b. Order Manager — [order_manager.py](trading_engine/order_manager.py)

**What happens:** Takes the strategy's decision and executes it as a **paper trade**.

**The full lifecycle:**
1. **Check balance** — Do we have enough USDT to buy? Do we hold enough BTC to sell?
2. **Create order** — `Order(side="BUY", symbol="BTC/USDT", quantity=0.1, price=40123.45, status="PENDING")`
3. **Fill order** — Since this is paper trading, orders fill instantly at current price. Status → `"FILLED"`
4. **Update position** — Track what we hold:
   - After buying 0.1 BTC @ $40,123.45 → position: `{BTC: 0.1, avg_entry: 40123.45}`
   - After selling 0.1 BTC @ $41,000 → position: `{BTC: 0.0}`, realized P&L: +$87.66
5. **Update balance** — Deduct USDT on buy, add USDT on sell
6. **Save to database**

**Position tracking calculates:**
- **Unrealized P&L** = $(current\_price - avg\_entry) \times quantity$ (for open positions)
- **Realized P&L** = profit/loss from closed trades

---

## Stage 5: Output — database + api

### 5a. Database — db.py + schema.sql

Everything gets persisted to SQLite:

| Table | What's stored |
|-------|---------------|
| `price_events` | Historical price data (for replay/analysis) |
| `orders` | Every order placed (BUY/SELL, price, status) |
| `alerts` | Every CEP signal triggered |
| `positions` | Current holdings and P&L |

### 5b. API — server.py

FastAPI exposes the data via REST endpoints so you could plug in a frontend later:

```
GET  /health          → is the system running?
GET  /prices/BTC-USDT → latest price for BTC
GET  /orders          → all orders (history)
POST /orders          → manually place a paper order
GET  /positions       → what do we hold? what's the P&L?
GET  /alerts          → recent CEP signals
```

---

## How main.py Ties It All Together

main.py is the **orchestrator**. In Phase 1, it's a simple loop:

```
initialize database
initialize CEP detectors (with configurable thresholds)
initialize trading engine (with $10,000 starting balance)

loop forever (or for N iterations):
    1. raw_data    = simulator.generate_tick()        # Stage 1
    2. event       = normalizer.normalize(raw_data)   # Stage 2
    3. alerts      = cep_engine.process(event)        # Stage 3 (all 3 detectors)
    4. decision    = strategy.evaluate(alerts, event)  # Stage 4a
    5. if decision:
           order   = order_manager.execute(decision)  # Stage 4b
    6. db.save(event, alerts, order)                  # Stage 5
    7. log everything to console
    8. sleep(1 second)
```

---

## Phase 2 — What Changes

In Phase 2, **Kafka** decouples the stages:

```
Simulator/Exchange → [Kafka topic: market_data] → Normalizer
Normalizer → [Kafka topic: price_events] → CEP Engine  
CEP Engine → [Kafka topic: alerts] → Trading Engine
Trading Engine → [Kafka topic: orders] → Database
```

Each component becomes an **independent consumer/producer** instead of direct function calls. This means:
- Components can run on separate machines
- If one crashes, the others keep running
- You can replay events from Kafka topics

But **the logic inside each stage stays exactly the same** — only the transport between stages changes. That's why Phase 1 works without Kafka.

---

## Summary: One Complete Cycle

> BTC is at $40,000. Simulator generates a new tick: $42,200 (+5.5%).
> 
> **Normalizer** wraps it in a `PriceEvent`.
> 
> **CEP Engine** runs all 3 detectors:
> - Moving average: short MA just crossed above long MA → `Alert(MA_CROSSOVER)`
> - Spike detector: 5.5% > 5% threshold → `Alert(SPIKE_DETECTED)`
> - Volume anomaly: volume normal → `None`
> 
> **Strategy** sees MA_CROSSOVER + SPIKE_DETECTED → decides **BUY**.
> 
> **Order Manager** checks balance ($10,000 USDT available), creates order for 0.1 BTC @ $42,200, fills it immediately. Balance: $5,780. Position: 0.1 BTC.
> 
> **Database** saves the price event, 2 alerts, and the filled order.
> 
> **API** can now serve: "You hold 0.1 BTC, entry $42,200, current $42,200, P&L: $0."


# How to run the code

## Dependencies (`requirement.txt`)

```
# Phase 1 — Local prototype
pydantic>=2.0
fastapi>=0.110.0
uvicorn>=0.29.0

# Phase 2 — Kafka streaming
kafka-python>=2.0.0
```

## Install
```bash
python -m pip install -r requirement.txt
```

## Run data stream simulator
```bash
python data_simulator/price_stream.py
```

## Run API server (Phase 1)
```bash
uvicorn api.api_server:app --reload --port 8000
```

## Run full pipeline
```bash
python main.py
```

## Run tests
```bash
pytest tests/ -v
```

# Run Commands

## Install dependencies
```
pip install -r requirement.txt
```
## Run modes (publisher / subscriber / standalone)

The `main.py` script now supports three run modes via `--mode`:

- `publisher`: collect data from the chosen `--source` and publish events over UDP to `127.0.0.1:9999` (use this in Terminal 1).
- `subscriber`: listen on UDP `127.0.0.1:9999`, run CEP detectors and the strategy (use this in Terminal 2).
- `standalone`: (single-process) placeholder; prefer running `publisher` and `subscriber` in separate terminals for development and testing.

### Publisher examples (Terminal 1)

Simulated data (default):
```
python main.py --mode publisher --source simulator
```

Real exchange (Binance):
```
python main.py --mode publisher --source binance
```

Real exchange (Coinbase):
```
python main.py --mode publisher --source coinbase
```

Use `--symbol` and `--interval` to change the trading pair and publish frequency:
```
python main.py --mode publisher --source binance --symbol ETH/USDT --interval 2
```

### Subscriber example (Terminal 2)

Listen for published events and run CEP + strategy:
```
python main.py --mode subscriber
```

### Standalone (single process)

Run publisher+subscriber in a single process (not recommended for development):
```
python main.py --mode standalone
```

### Open 2 processes
```
python run_bot.py
```

### Notes

- Publisher sends UDP packets to `127.0.0.1:9999` by default — ensure this port is available.
- Open two terminals: run the `publisher` in one and the `subscriber` in the other to simulate decoupled components.
- Example combined command (publisher in background / separate shell):
```
# Terminal 1
python main.py --mode publisher --source binance

# Terminal 2
python main.py --mode subscriber
```

## Run API server (Phase 1) then 👉 http://localhost:8000/docs
```
uvicorn api.api_server:app --reload --port 8000
```

## Run tests
```
pytest tests/ -v
```

## See all options
```
python main.py --help
```
