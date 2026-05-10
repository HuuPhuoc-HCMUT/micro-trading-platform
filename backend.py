import argparse
import logging
import os
import queue
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from cep_engine.moving_average import MovingAverageDetector
from cep_engine.probabilistic_signal import ProbabilisticSignalDetector
from cep_engine.spike_detector import SpikeDetector
from cep_engine.volume_anomaly import VolumeAnomalyDetector
from data_simulator.price_stream import generate_price_stream
from data_source.binance_client import iter_binance_trades
from data_source.coinbase_client import iter_coinbase_trades
from data_source.exness_history import iter_exness_ticks
from data_source.kraken_client import iter_kraken_trades
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager
from trading_engine.strategy import RuleBasedStrategy


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_SYMBOLS = "BTC/USDT,ETH/USDT,SOL/USDT"
UP_ARROW = "↑"
DOWN_ARROW = "↓"
FLAT_ARROW = "→"
logger = logging.getLogger(__name__)


def parse_symbols(values: list[str]) -> dict[str, float]:
    parsed: dict[str, float] = {}
    for value in values:
        if "=" in value:
            symbol, start = value.split("=", 1)
            parsed[symbol.strip().upper()] = float(start)
        else:
            parsed[value.strip().upper()] = 0.0
    return parsed


def parse_sources(value: str) -> list[str]:
    normalized = value.replace(",", "+").replace(" ", "")
    parts = [part.strip().lower() for part in normalized.split("+") if part.strip()]
    valid = {"simulator", "exness", "binance", "coinbase", "kraken"}
    result: list[str] = []
    for part in parts or ["binance"]:
        if part not in valid:
            raise ValueError(f"Unsupported source: {part}")
        if part not in result:
            result.append(part)
    return result


def floor_timestamp(ts: datetime, interval_seconds: int) -> datetime:
    epoch = int(ts.timestamp())
    floored = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(floored, tz=ts.tzinfo or timezone.utc)


def trend_from_candles(candles: list[dict[str, object]]) -> dict[str, object]:
    if not candles:
        return {"label": "FLAT", "arrow": FLAT_ARROW, "change": 0.0}

    if len(candles) == 1:
        open_price = float(candles[-1]["open"])
        close_price = float(candles[-1]["close"])
        change = 0.0 if open_price == 0 else (close_price - open_price) / open_price
    else:
        previous_close = float(candles[-2]["close"])
        current_close = float(candles[-1]["close"])
        change = 0.0 if previous_close == 0 else (current_close - previous_close) / previous_close

    if change > 0:
        return {"label": "UP", "arrow": UP_ARROW, "change": change}
    if change < 0:
        return {"label": "DOWN", "arrow": DOWN_ARROW, "change": change}
    return {"label": "FLAT", "arrow": FLAT_ARROW, "change": 0.0}


def _serialize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [_serialize(item) for item in value]
    return value


class MarketBackendEngine:
    def __init__(
        self,
        symbols: dict[str, float],
        interval: float,
        source: str,
        candle_seconds: int,
        save_to_db: bool,
        exness_file: str | None = None,
        exness_url: str | None = None,
        history_limit: int = 120,
    ) -> None:
        self.symbols = symbols
        self.interval = interval
        self.source = source
        self.sources = parse_sources(source)
        self.candle_seconds = max(int(candle_seconds), 1)
        self.history_limit = max(int(history_limit), 10)
        self.exness_file = exness_file
        self.exness_url = exness_url

        self.order_manager = OrderManager(initial_balance=10000.0, persist_to_db=save_to_db)
        self.strategy = RuleBasedStrategy(self.order_manager)
        self.ma_detector = MovingAverageDetector(short_window=15, long_window=60)
        self.spike_detector = SpikeDetector(threshold_percent=2.0, window_size=5)
        self.volume_detector = VolumeAnomalyDetector(window_size=10, multiplier=3.0)
        self.prob_detector = ProbabilisticSignalDetector(
            window_size=60,
            min_history=20,
            learning_rate=0.06,
            signal_threshold=0.20,
        )

        self.price_history = {symbol: deque(maxlen=30) for symbol in symbols}
        self.volume_history = {symbol: deque(maxlen=30) for symbol in symbols}
        self.latest_price = symbols.copy()
        self.latest_source = {symbol: "+".join(self.sources) for symbol in symbols}
        self.symbol_sources_seen = {symbol: set(self.sources) if len(self.sources) == 1 else set() for symbol in symbols}
        self.symbol_source_last_seen = {symbol: {} for symbol in symbols}
        self.source_event_counts = {source_name: 0 for source_name in self.sources}
        self.source_last_timestamp: dict[str, datetime | None] = {source_name: None for source_name in self.sources}
        self.latest_analysis: dict[str, dict[str, float | bool | str]] = {}
        self.recent_alert_feed: deque[str] = deque(maxlen=10)
        self.closed_candles = {symbol: deque(maxlen=self.history_limit) for symbol in symbols}
        self.current_candles: dict[str, dict[str, object]] = {}
        self.focus_symbol: str | None = next(iter(symbols), None)
        self.paused = False
        self.exhausted = False
        self.tick_count = 0
        self._simulator_prices = {symbol: price or 100.0 for symbol, price in symbols.items()}
        self.stream = self._build_stream()

    def _build_stream(self) -> Iterator[PriceEvent]:
        streams: list[Iterator[PriceEvent]] = []
        symbols = list(self.symbols.keys())

        if "exness" in self.sources:
            streams.append(iter_exness_ticks(symbols=symbols, file_path=self.exness_file, url=self.exness_url))
        if "binance" in self.sources:
            streams.append(iter_binance_trades(symbols))
        if "coinbase" in self.sources:
            streams.append(iter_coinbase_trades(symbols))
        if "kraken" in self.sources:
            streams.append(iter_kraken_trades(symbols))
        if "simulator" in self.sources:
            streams.append(generate_price_stream(self._simulator_prices, interval=self.interval, copy_prices=False))

        if len(streams) == 1:
            return streams[0]
        return self._merge_streams(streams)

    def _merge_streams(self, streams: list[Iterator[PriceEvent]]) -> Iterator[PriceEvent]:
        event_queue: queue.Queue[PriceEvent | None] = queue.Queue()
        finished_count = 0

        def pump(stream: Iterator[PriceEvent]) -> None:
            try:
                for event in stream:
                    event_queue.put(event)
            finally:
                event_queue.put(None)

        for stream in streams:
            threading.Thread(target=pump, args=(stream,), daemon=True).start()

        while finished_count < len(streams):
            item = event_queue.get()
            if item is None:
                finished_count += 1
                continue
            yield item

    def _append_candle_event(self, event: PriceEvent) -> None:
        candle = {
            "timestamp": event.timestamp,
            "open": float(event.open if event.open is not None else event.price),
            "high": float(event.high if event.high is not None else event.price),
            "low": float(event.low if event.low is not None else event.price),
            "close": float(event.price),
            "volume": float(event.volume),
            "ticks": int(event.ticks_count if event.ticks_count is not None else 1),
        }
        self.closed_candles[event.symbol].append(candle)

    def _update_candles(self, event: PriceEvent) -> None:
        if event.open is not None and event.high is not None and event.low is not None:
            self._append_candle_event(event)
            return

        bucket_start = floor_timestamp(event.timestamp, self.candle_seconds)
        current = self.current_candles.get(event.symbol)
        price = float(event.price)
        volume = float(event.volume)

        if current is None:
            self.current_candles[event.symbol] = {
                "timestamp": bucket_start,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
                "volume": volume,
                "ticks": 1,
            }
            return

        if current["timestamp"] == bucket_start:
            current["high"] = max(float(current["high"]), price)
            current["low"] = min(float(current["low"]), price)
            current["close"] = price
            current["volume"] = float(current["volume"]) + volume
            current["ticks"] = int(current["ticks"]) + 1
            return

        self.closed_candles[event.symbol].append(current.copy())
        self.current_candles[event.symbol] = {
            "timestamp": bucket_start,
            "open": price,
            "high": price,
            "low": price,
            "close": price,
            "volume": volume,
            "ticks": 1,
        }

    def _candles_for_symbol(self, symbol: str) -> list[dict[str, object]]:
        candles = list(self.closed_candles[symbol])
        current = self.current_candles.get(symbol)
        if current:
            candles.append(current.copy())
        return candles

    def _process_event(self, event: PriceEvent) -> None:
        alerts = []
        for detector in (self.ma_detector, self.spike_detector, self.volume_detector, self.prob_detector):
            alert = detector.process(event)
            if alert:
                alerts.append(alert)

        self.latest_price[event.symbol] = event.price
        self.latest_source[event.symbol] = event.source
        self.symbol_sources_seen.setdefault(event.symbol, set()).add(event.source)
        self.symbol_source_last_seen.setdefault(event.symbol, {})[event.source] = event.timestamp
        self.source_event_counts[event.source] = self.source_event_counts.get(event.source, 0) + 1
        self.source_last_timestamp[event.source] = event.timestamp
        self.price_history[event.symbol].append(event.price)
        self.volume_history[event.symbol].append(event.volume)
        self._update_candles(event)
        self.latest_analysis[event.symbol] = self.strategy.analyze(alerts, event.symbol) if alerts else {
            "edge": 0.0,
            "expected_return": 0.0,
            "confidence": 0.0,
            "realized_volatility": 0.0,
            "downside_volatility": 0.0,
            "has_volume_confirmation": False,
            "action": "HOLD",
        }

        for alert in alerts:
            direction = alert.direction or "INFO"
            self.recent_alert_feed.appendleft(
                f"{event.symbol:<9} {direction:<4} {alert.signal_type:<20} {alert.message[:80]}"
            )

        self.strategy.execute(alerts, event)

    def symbol_rows(self) -> list[dict[str, object]]:
        rows = []
        positions = self.order_manager.get_portfolio_summary(self.latest_price)["positions"]

        for symbol in self.symbols:
            candles = self._candles_for_symbol(symbol)
            latest_candle = candles[-1] if candles else None
            analysis = self.latest_analysis.get(symbol, {
                "edge": 0.0,
                "confidence": 0.0,
                "expected_return": 0.0,
                "action": "HOLD",
            })
            trend = trend_from_candles(candles)
            position = positions.get(symbol, {})

            rows.append({
                "symbol": symbol,
                "source": self.latest_source.get(symbol, "+".join(self.sources)),
                "active_sources": sorted(self.symbol_sources_seen.get(symbol, set())),
                "change": float(trend["change"]),
                "trend_label": str(trend["label"]),
                "trend_arrow": str(trend["arrow"]),
                "edge": float(analysis.get("edge", 0.0)),
                "confidence": float(analysis.get("confidence", 0.0)),
                "expected_return": float(analysis.get("expected_return", 0.0)),
                "action": str(analysis.get("action", "HOLD")),
                "position_qty": float(position.get("quantity", 0.0)),
                "position_side": str(position.get("side", "FLAT")),
                "avg_entry": float(position.get("avg_entry", 0.0)),
                "unrealized": float(position.get("unrealized_pnl", 0.0)),
                "open": float(latest_candle["open"]) if latest_candle else 0.0,
                "high": float(latest_candle["high"]) if latest_candle else 0.0,
                "low": float(latest_candle["low"]) if latest_candle else 0.0,
                "close": float(latest_candle["close"]) if latest_candle else float(self.latest_price.get(symbol, 0.0)),
                "candle_volume": float(latest_candle["volume"]) if latest_candle else 0.0,
                "ticks": int(latest_candle["ticks"]) if latest_candle else 0,
                "candles": candles[-10:],
            })

        rows.sort(key=lambda item: str(item["symbol"]))
        return rows

    def step(self) -> None:
        if self.paused or self.exhausted:
            return
        try:
            event = next(self.stream)
        except StopIteration:
            self.exhausted = True
            self.paused = True
            return
        self.tick_count += 1
        self._process_event(event)

    def snapshot(self) -> dict[str, object]:
        rows = self.symbol_rows()
        focus_row = next((row for row in rows if row["symbol"] == self.focus_symbol), rows[0] if rows else None)
        if focus_row:
            self.focus_symbol = str(focus_row["symbol"])
        return {
            "summary": self.order_manager.get_portfolio_summary(self.latest_price),
            "rows": rows,
            "recent_signals": list(self.recent_alert_feed),
            "paused": self.paused,
            "tick_count": self.tick_count,
            "source": self.source,
            "sources": list(self.sources),
            "exhausted": self.exhausted,
            "focus_symbol": self.focus_symbol,
            "focus_candles": focus_row["candles"] if focus_row else [],
        }

    def set_focus_symbol(self, symbol: str) -> bool:
        normalized = symbol.strip().upper()
        if normalized in self.symbols:
            self.focus_symbol = normalized
            return True
        return False


class DashboardBackend:
    def __init__(
        self,
        symbols: dict[str, float],
        interval: float,
        source: str,
        candle_seconds: int,
        save_to_db: bool,
        exness_file: str | None = None,
        exness_url: str | None = None,
    ) -> None:
        self.engine = MarketBackendEngine(
            symbols=symbols,
            interval=interval,
            source=source,
            candle_seconds=candle_seconds,
            save_to_db=save_to_db,
            exness_file=exness_file,
            exness_url=exness_url,
        )
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self.last_error: str | None = None

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                with self._lock:
                    self.engine.step()
                    self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)
                logger.exception("Backend stream error")
                time.sleep(1)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            payload = _serialize(self.engine.snapshot())
        payload["last_error"] = self.last_error
        return payload

    def rows(self) -> list[dict[str, Any]]:
        return list(self.snapshot()["rows"])

    def summary(self) -> dict[str, Any]:
        return dict(self.snapshot()["summary"])

    def orders(self, limit: int = 20, symbol: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            history = self.engine.order_manager.order_history
            if symbol:
                normalized = symbol.strip().upper()
                history = [order for order in history if order.symbol.upper() == normalized]
            history = list(reversed(history[-limit:]))
        return _serialize([
            {
                "id": index,
                "symbol": order.symbol,
                "side": order.side,
                "quantity": order.quantity,
                "price": order.price,
                "status": order.status,
                "trade_pnl": order.trade_pnl,
                "timestamp": order.timestamp,
            }
            for index, order in enumerate(history, start=1)
        ])

    def candles(self, symbol: str | None = None, limit: int = 120) -> list[dict[str, Any]]:
        with self._lock:
            target_symbol = symbol or self.engine.focus_symbol or next(iter(self.engine.symbols))
            if target_symbol not in self.engine.symbols:
                raise KeyError(target_symbol)
            candles = self.engine._candles_for_symbol(target_symbol)[-limit:]
        return _serialize([
            {
                "time": int(candle["timestamp"].timestamp()),
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
                "ticks": candle["ticks"],
                "timestamp": candle["timestamp"],
            }
            for candle in candles
        ])

    def focus(self, symbol: str) -> bool:
        with self._lock:
            return self.engine.set_focus_symbol(symbol)

    def row_for_symbol(self, symbol: str | None = None) -> dict[str, Any] | None:
        target = (symbol or self.engine.focus_symbol or "").strip().upper()
        rows = self.rows()
        return next((row for row in rows if row["symbol"] == target), rows[0] if rows else None)


def _build_backend() -> DashboardBackend:
    symbols_text = os.getenv("DASHBOARD_SYMBOLS", DEFAULT_SYMBOLS)
    source = os.getenv("DASHBOARD_SOURCE", "binance")
    interval = float(os.getenv("DASHBOARD_INTERVAL", "0.15"))
    candle_seconds = int(os.getenv("DASHBOARD_CANDLE_SECONDS", "60"))
    save_to_db = os.getenv("DASHBOARD_SAVE_TO_DB", "false").lower() == "true"
    exness_file = os.getenv("DASHBOARD_EXNESS_FILE")
    exness_url = os.getenv("DASHBOARD_EXNESS_URL")
    symbols = parse_symbols([item.strip() for item in symbols_text.split(",") if item.strip()])
    return DashboardBackend(
        symbols=symbols,
        interval=interval,
        source=source,
        candle_seconds=candle_seconds,
        save_to_db=save_to_db,
        exness_file=exness_file,
        exness_url=exness_url,
    )


backend = _build_backend()


@asynccontextmanager
async def lifespan(_: FastAPI):
    backend.start()
    yield
    backend.stop()


app = FastAPI(
    title="Micro Trading Backend",
    description="Backend toi gian cho web dashboard.",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_index() -> FileResponse:
    return FileResponse(ROOT_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/state")
def get_state() -> dict[str, Any]:
    return backend.snapshot()


@app.get("/api/portfolio")
def get_portfolio() -> dict[str, Any]:
    return backend.summary()


@app.get("/api/symbols")
def get_symbols() -> list[dict[str, Any]]:
    return backend.rows()


@app.get("/api/orders")
def get_orders(limit: int = Query(default=20, ge=1, le=200), symbol: str | None = None) -> list[dict[str, Any]]:
    return backend.orders(limit=limit, symbol=symbol)


@app.get("/api/signals")
def get_signals(limit: int = Query(default=10, ge=1, le=50)) -> list[str]:
    return list(backend.snapshot()["recent_signals"])[:limit]


@app.get("/api/candles")
def get_candles(symbol: str | None = None, limit: int = Query(default=120, ge=1, le=500)) -> list[dict[str, Any]]:
    try:
        return backend.candles(symbol=symbol, limit=limit)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {exc.args[0]}") from exc


@app.get("/api/focus")
def get_focus(symbol: str | None = None) -> dict[str, Any]:
    row = backend.row_for_symbol(symbol)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Symbol not found: {symbol}")
    return row


@app.post("/api/focus")
def set_focus(symbol: str = Query(...)) -> dict[str, Any]:
    normalized = symbol.strip().upper()
    if not backend.focus(normalized):
        raise HTTPException(status_code=404, detail=f"Symbol not found: {normalized}")
    return {"focus_symbol": normalized}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the backend for the trading dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host")
    parser.add_argument("--port", type=int, default=8000, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Enable auto reload")
    args = parser.parse_args()
    uvicorn.run("backend:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
