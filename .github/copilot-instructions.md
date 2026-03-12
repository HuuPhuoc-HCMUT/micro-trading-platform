# Copilot Instructions — Micro Trading Platform

## Project Overview

University mini-project: a **Complex Event Processing (CEP) crypto trading platform** (paper trading).
Pipeline: `Market Data → Normalizer → CEP Engine → Trading Engine → Output (API + DB)`

**Goal:** A working local prototype in 1 month with 4 team members.
- **Task A:** Market data simulator + dataset
- **Task B:** CEP engine (moving average, spike detection, volume anomaly)
- **Task C:** Trading engine (rule-based strategy, order simulation, position tracking)
- **Task D:** Pipeline integration, logging, API, database

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11 |
| Web Framework | FastAPI |
| Data Models | Pydantic v2 (BaseModel) |
| Database | SQLite via `sqlite3` stdlib |
| Streaming | kafka-python (phase 2 — after local prototype works) |
| Testing | pytest |
| Linting/Formatting | ruff |
| Logging | Python `logging` stdlib |
| Containerization | Docker + docker-compose (phase 2) |
| Package Manager | pip + requirement.txt |

## Architecture & Data Flow

```
Data Simulator / Exchange Client
        │
        ▼
   Normalizer (data_source/normalizer.py)
        │  → PriceEvent model
        ▼
   CEP Engine (cep_engine/)
   ├─ moving_average.py
   ├─ spike_detector.py
   └─ volume_anomaly.py
        │  → Alert model
        ▼
   Trading Engine (trading_engine/)
   ├─ strategy.py        (buy/sell decision rules)
   └─ order_manager.py   (order lifecycle, position tracking)
        │  → Order model
        ▼
   Database (database/)   ← persist orders, positions, P&L
        │
        ▼
   API Server (api/server.py)  ← expose data via REST
```

### Phase 1 — Local Prototype (Weeks 1–2)
- Data simulator generates price stream (no Kafka, no exchange APIs)
- CEP engine processes events in-memory via function calls
- Trading engine executes paper trades
- Results logged to console + SQLite

### Phase 2 — Integration (Weeks 3–4)
- Replace simulator with real exchange clients (Binance/Coinbase/Kraken)
- Add Kafka streaming between components
- Add FastAPI endpoints
- Add Docker setup

## Project Structure

```
micro-trading-platform/
├── main.py                    # Entry point — runs the pipeline
├── requirement.txt           # Python dependencies
├── api/
│   └── server.py              # FastAPI app and route definitions
├── cep_engine/
│   ├── moving_average.py      # Moving average crossover detection
│   ├── spike_detector.py      # Sudden price spike detection
│   └── volume_anomaly.py      # Abnormal volume detection
├── data_simulator/
│   └── price_stream.py        # Generates synthetic OHLCV price data
├── data_source/
│   ├── binance_client.py      # Binance exchange API client
│   ├── coinbase_client.py     # Coinbase exchange API client
│   ├── kraken_client.py       # Kraken exchange API client
│   └── normalizer.py          # Standardize data from any source
├── database/
│   ├── db.py                  # SQLite connection + CRUD helpers
│   └── schema.sql             # CREATE TABLE statements
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yaml
├── models/
│   ├── price_event.py         # PriceEvent Pydantic model
│   ├── order.py               # Order Pydantic model
│   └── alert.py               # Alert/Signal Pydantic model
├── streaming/
│   ├── kafka_producer.py      # Publish events to Kafka topics
│   └── kafka_consumer.py      # Consume events from Kafka topics
├── trading_engine/
│   ├── strategy.py            # Rule-based trading strategies
│   └── order_manager.py       # Order creation, execution, position tracking
└── tests/                     # pytest test files (mirror src structure)
```

## Coding Conventions

### General Rules
- **Python 3.11**, use modern syntax (match/case OK, f-strings preferred)
- **Sync-first** — no async/await unless specifically needed (Kafka consumer, FastAPI endpoints)
- Keep functions small (< 40 lines). One function = one job.
- No premature abstraction — write concrete code first, abstract only when duplication appears 3+ times

### Type Hints
- Required on all function signatures (parameters + return type)
- Not required for local variables (let inference work)
```python
def calculate_sma(prices: list[float], window: int) -> float:
    return sum(prices[-window:]) / window
```

### Naming
- Files: `snake_case.py`
- Classes: `PascalCase`
- Functions/variables: `snake_case`
- Constants: `UPPER_SNAKE_CASE`
- Private helpers: prefix with `_`

### Models (Pydantic v2)
All data structures in `models/` use Pydantic `BaseModel`:
```python
from pydantic import BaseModel
from datetime import datetime

class PriceEvent(BaseModel):
    symbol: str                # e.g. "BTC/USDT"
    price: float
    volume: float
    timestamp: datetime
    source: str                # e.g. "simulator", "binance"
```

### Imports
- stdlib → third-party → local, separated by blank lines
- Use absolute imports from project root: `from models.price_event import PriceEvent`

### Logging
Use `logging` stdlib, one logger per module:
```python
import logging
logger = logging.getLogger(__name__)

logger.info("Processing %s at price %.2f", event.symbol, event.price)
```
- Use `%s` formatting (not f-strings) in log calls for performance
- Levels: DEBUG for internals, INFO for pipeline flow, WARNING for anomalies, ERROR for failures

### Error Handling
- Validate at system boundaries (API input, exchange responses, file I/O)
- Don't wrap internal function calls in try/except unless recovery is possible
- Let errors propagate with meaningful context

### Docstrings
Google style, only on public functions and classes:
```python
def detect_spike(prices: list[float], threshold: float) -> bool:
    """Check if the latest price change exceeds the threshold.

    Args:
        prices: Recent price history.
        threshold: Maximum allowed percentage change (e.g., 0.05 for 5%).

    Returns:
        True if a spike is detected.
    """
```

## Data Models Reference

### PriceEvent
```python
class PriceEvent(BaseModel):
    symbol: str           # "BTC/USDT", "ETH/USDT"
    price: float
    volume: float
    timestamp: datetime
    source: str           # "simulator" | "binance" | "coinbase" | "kraken"
```

### Alert
```python
class Alert(BaseModel):
    signal_type: str      # "MA_CROSSOVER" | "SPIKE_DETECTED" | "VOLUME_ANOMALY"
    symbol: str
    severity: str         # "LOW" | "MEDIUM" | "HIGH"
    message: str
    triggered_at: datetime
```

### Order
```python
class Order(BaseModel):
    id: str               # UUID
    symbol: str
    side: str             # "BUY" | "SELL"
    quantity: float
    price: float
    status: str           # "PENDING" | "FILLED" | "CANCELLED"
    created_at: datetime
```

## Database Schema (SQLite)

Tables: `price_events`, `orders`, `alerts`, `positions`
- Use parameterized queries (`?` placeholders) — **never** use f-strings for SQL
- Keep `db.py` as a thin wrapper with functions like `insert_order()`, `get_positions()`

## API Endpoints (FastAPI)

```
GET  /health                 → {"status": "ok"}
GET  /prices/{symbol}        → latest price data
GET  /orders                 → list all orders
POST /orders                 → place a new paper order
GET  /positions              → current positions and P&L
GET  /alerts                 → recent CEP signals
```

## Trading Rules (Paper Trading Only)

- **No real money.** All trades are simulated.
- Starting balance: configurable (default $10,000 USDT)
- No leverage — spot trading only
- Cannot buy if insufficient balance
- Cannot sell if no position held
- Order fills immediately at current market price (simplified execution)

## CEP Engine Rules

Each detector in `cep_engine/` takes a list of recent `PriceEvent`s and returns an `Alert` or `None`:

1. **Moving Average Crossover** — short MA crosses above/below long MA
2. **Price Spike Detection** — price change exceeds X% within N seconds
3. **Volume Anomaly** — volume exceeds Y× the rolling average

Thresholds should be configurable parameters, not hardcoded.

## Testing

- Test files in `tests/` directory, named `test_<module>.py`
- Use pytest fixtures for common test data (sample PriceEvents, etc.)
- Minimum: unit tests for each CEP detector and trading strategy function
- Run: `pytest tests/ -v`

## Running the Project

```bash
# Install dependencies
pip install -r requirement.txt

# Run local prototype (Phase 1)
python main.py

# Run API server
uvicorn api.api_server:app --reload --port 8000

# Run tests
pytest tests/ -v

# Lint
ruff check .
ruff format .
```

## Key Reminders for Copilot

- This is a **student prototype** — favor simplicity and clarity over enterprise patterns
- When generating code, always use the models from `models/` — don't create ad-hoc dicts
- All SQL must use parameterized queries (security requirement)
- Prefer stdlib solutions before adding dependencies
- Keep Kafka integration isolated in `streaming/` — Phase 1 should work without Kafka
- When in doubt, log more rather than less
- Comments in English, README/docs can be Vietnamese