from contextlib import asynccontextmanager
import asyncio
from datetime import datetime
import os
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

from database.db import init_db
from streaming.kafka_consumer import KafkaEventConsumer
from streaming.serializers import deserialize_price_event
from streaming.topics import PRICE_EVENTS_TOPIC

KAFKA_BOOTSTRAP_SERVERS: str = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# ---------------------------------------------------------------------------
# SSE broadcaster: one background Kafka consumer pushes candles to all clients
# ---------------------------------------------------------------------------
_sse_clients: list[asyncio.Queue] = []
_broadcaster_task: asyncio.Task | None = None


async def _kafka_candle_broadcaster() -> None:
    """Background task: poll price_events and fan out to all SSE queues."""
    consumer = KafkaEventConsumer(
        topics=[PRICE_EVENTS_TOPIC],
        group_id="sse-broadcaster",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="latest",
    )
    consumer.connect()
    loop = asyncio.get_event_loop()

    def _poll() -> list[bytes]:
        records = consumer._consumer.poll(timeout_ms=200)
        return [msg.value for batch in records.values() for msg in batch]

    try:
        while True:
            msgs = await loop.run_in_executor(None, _poll)
            for raw in msgs:
                try:
                    event = deserialize_price_event(raw)
                    payload = event.model_dump_json()
                    for q in list(_sse_clients):
                        q.put_nowait(payload)
                except Exception:
                    pass
    except asyncio.CancelledError:
        consumer.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _broadcaster_task
    init_db()
    _broadcaster_task = asyncio.create_task(_kafka_candle_broadcaster())
    yield
    if _broadcaster_task:
        _broadcaster_task.cancel()
        try:
            await _broadcaster_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    lifespan=lifespan,
    title="Micro Trading API",
    description="API cung cấp dữ liệu cho Dashboard giao dịch",
    version="1.0.0"
)

# Cấu hình CORS để cho phép Giao diện Web (HTML/JS) gọi API mà không bị chặn
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = "trading_platform.db"

# In-memory state cho focus symbol (không cần persist qua restart)
_focus_symbol: str = "BTC/USDT"
_tick_count: int = 0

def query_db(query: str, args: tuple = (), one: bool = False):
    """Hàm hỗ trợ chọc thẳng vào SQLite lấy dữ liệu ra dạng Dictionary."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row  # Trả về kết quả có tên cột thay vì chỉ có index
        cursor = conn.cursor()
        cursor.execute(query, args)
        rv = cursor.fetchall()
        return (dict(rv[0]) if rv else None) if one else [dict(r) for r in rv]

@app.get("/")
def read_root():
    """Serve the trading dashboard HTML."""
    html_path = Path(__file__).parent.parent / "index.html"
    return FileResponse(html_path, media_type="text/html")

INITIAL_BALANCE: float = 10_000.0


def _balance_from_orders() -> tuple[float, float]:
    """Derive cash balance and realized PnL from the orders table.

    This is the only consistent source when multiple strategy workers run in
    parallel — each worker has its own in-memory OrderManager, so
    balance_history rows come from different isolated instances and cannot be
    mixed.  The orders table contains every filled trade from every worker,
    which gives a single coherent view of the shared account.

    Returns:
        (balance_usdt, realized_pnl)
    """
    row = query_db(
        """
        SELECT
            ? + COALESCE(SUM(
                CASE WHEN side = 'SELL' THEN  quantity * price
                     ELSE                    -quantity * price
                END
            ), 0.0) AS balance_usdt,
            COALESCE(SUM(
                CASE WHEN trade_pnl IS NOT NULL THEN trade_pnl ELSE 0.0 END
            ), 0.0) AS realized_pnl
        FROM orders
        WHERE status = 'FILLED'
        """,
        (INITIAL_BALANCE,),
        one=True,
    )
    if row and row['balance_usdt'] is not None:
        return row['balance_usdt'], row['realized_pnl']
    return INITIAL_BALANCE, 0.0


@app.get("/api/portfolio")
def get_portfolio():
    """Lấy số dư tài khoản và PnL mới nhất."""
    balance_usdt, realized_pnl = _balance_from_orders()
    return {
        "balance_usdt": balance_usdt,
        "realized_pnl": realized_pnl,
    }

@app.get("/api/orders")
def get_orders(limit: int = 50, symbol: str | None = None):
    """Lấy lịch sử lệnh giao dịch gần nhất, tuỳ chọn lọc theo symbol."""
    if symbol:
        orders = query_db(
            "SELECT * FROM orders WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit)
        )
    else:
        orders = query_db(
            "SELECT * FROM orders ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    return orders

@app.get("/api/candles")
def get_candles(limit: int = 100, symbol: str | None = None):
    """Lấy dữ liệu nến OHLC để vẽ biểu đồ TradingView, tuỳ chọn lọc theo symbol."""
    if symbol:
        candles = query_db(
            "SELECT * FROM (SELECT * FROM price_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
            (symbol, limit)
        )
    else:
        candles = query_db(
            "SELECT * FROM (SELECT * FROM price_history ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC",
            (limit,)
        )

    formatted_candles = []
    for c in candles:
        dt = datetime.fromisoformat(c['timestamp'].replace("Z", "+00:00"))
        formatted_candles.append({
            "time": int(dt.timestamp()),
            "timestamp": c['timestamp'],
            "open": c['open'],
            "high": c['high'],
            "low": c['low'],
            "close": c['close'],
            "volume": c['volume'],
            "ticks": c.get('ticks_count', 1),
        })

    return formatted_candles


@app.get("/api/alerts")
def get_alerts(limit: int = 20):
    """Lấy danh sách tín hiệu CEP gần nhất."""
    alerts = query_db(
        "SELECT * FROM alerts ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    return alerts


@app.get("/api/signals")
def get_signals(limit: int = 8, symbol: str | None = None):
    """Trả về danh sách chuỗi tín hiệu CEP để hiển thị trên dashboard."""
    if symbol:
        alerts = query_db(
            "SELECT triggered_at, signal_type, symbol, severity, message FROM alerts WHERE symbol = ? ORDER BY id DESC LIMIT ?",
            (symbol, limit)
        )
    else:
        alerts = query_db(
            "SELECT triggered_at, signal_type, symbol, severity, message FROM alerts ORDER BY id DESC LIMIT ?",
            (limit,)
        )
    return [
        f"[{a['signal_type']}] {a['symbol']} — {a['message']}"
        for a in alerts
    ]


@app.get("/api/focus")
def get_focus(symbol: str | None = None):
    """Lấy thông tin chi tiết của symbol đang focus (giá mới nhất, xu hướng, PnL)."""
    global _focus_symbol
    target = symbol or _focus_symbol

    latest = query_db(
        "SELECT * FROM price_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
        (target,),
        one=True
    )
    prev = query_db(
        "SELECT close FROM price_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1 OFFSET 1",
        (target,),
        one=True
    )

    if not latest:
        return {"symbol": target, "close": 0.0, "change": 0.0, "trend_arrow": "→", "trend_label": "Flat",
                "source": "simulator", "edge": 0.0, "confidence": 0.0, "unrealized": 0.0, "position_qty": 0.0}

    close = latest['close']
    prev_close = prev['close'] if prev else close
    change = (close - prev_close) / prev_close if prev_close else 0.0

    if change > 0.001:
        trend_arrow, trend_label = "↑", "Up"
    elif change < -0.001:
        trend_arrow, trend_label = "↓", "Down"
    else:
        trend_arrow, trend_label = "→", "Flat"

    # Lấy tín hiệu mới nhất để lấy edge / confidence
    latest_alert = query_db(
        "SELECT score, confidence, direction FROM alerts WHERE symbol = ? ORDER BY triggered_at DESC LIMIT 1",
        (target,),
        one=True
    )
    edge = latest_alert['score'] if latest_alert else 0.0
    confidence = latest_alert['confidence'] if latest_alert else 0.0

    # Lấy vị thế đang mở từ DB (được lưu bởi strategy worker sau mỗi lệnh)
    pos_row = query_db(
        "SELECT quantity, avg_entry FROM positions WHERE symbol = ?",
        (target,),
        one=True,
    )
    pos_qty = pos_row['quantity'] if pos_row else 0.0
    avg_entry = pos_row['avg_entry'] if pos_row else 0.0
    # unrealized = qty * (current_price - avg_entry)  — works for both long (qty>0) and short (qty<0)
    unrealized = pos_qty * (close - avg_entry) if avg_entry and pos_qty else 0.0

    return {
        "symbol": target,
        "source": "simulator",
        "close": close,
        "change": change,
        "trend_arrow": trend_arrow,
        "trend_label": trend_label,
        "edge": edge,
        "confidence": confidence,
        "unrealized": unrealized,
        "position_qty": pos_qty,
    }


@app.post("/api/focus")
def set_focus(symbol: str = Query(...)):
    """Đặt symbol cần theo dõi trên dashboard."""
    global _focus_symbol
    _focus_symbol = symbol
    return {"focus_symbol": _focus_symbol}


@app.get("/api/state")
def get_state():
    """Trả về trạng thái tổng hợp của platform cho dashboard."""
    global _focus_symbol, _tick_count

    # Số dư — reconstruct from orders for multi-worker consistency
    balance_usdt, realized_pnl = _balance_from_orders()

    # Số tick tổng
    tick_row = query_db("SELECT COUNT(*) as cnt FROM price_history", one=True)
    _tick_count = tick_row['cnt'] if tick_row else 0

    # Lấy danh sách symbol có dữ liệu gần nhất
    symbol_rows = query_db(
        """
        SELECT symbol, close, open,
               (close - open) / NULLIF(open, 0) AS change
        FROM price_history
        WHERE timestamp IN (
            SELECT MAX(timestamp) FROM price_history GROUP BY symbol
        )
        ORDER BY symbol
        """
    )

    # Lấy tín hiệu mới nhất mỗi symbol để hiển thị action / edge
    alert_rows = query_db(
        """
        SELECT symbol, direction, score, confidence
        FROM alerts
        WHERE triggered_at IN (SELECT MAX(triggered_at) FROM alerts GROUP BY symbol)
        """
    )
    alert_map = {a['symbol']: a for a in alert_rows}

    rows = []
    for r in symbol_rows:
        change = r.get('change') or 0.0
        if change > 0.001:
            trend_arrow, trend_label = "↑", "Up"
        elif change < -0.001:
            trend_arrow, trend_label = "↓", "Down"
        else:
            trend_arrow, trend_label = "→", "Flat"
        alert = alert_map.get(r['symbol'], {})
        rows.append({
            "symbol": r['symbol'],
            "source": "simulator",
            "active_sources": ["simulator"],
            "close": r['close'],
            "change": change,
            "trend_arrow": trend_arrow,
            "trend_label": trend_label,
            "action": alert.get('direction') or 'HOLD',
            "edge": alert.get('score') or 0.0,
            "confidence": alert.get('confidence') or 0.0,
        })

    # Đặt focus_symbol mặc định nếu chưa có
    if not _focus_symbol and rows:
        _focus_symbol = rows[0]['symbol']

    # Tính tổng unrealized PnL và market value từ tất cả vị thế đang mở
    pos_rows = query_db("SELECT symbol, quantity, avg_entry FROM positions WHERE quantity != 0")
    total_unrealized_pnl = 0.0
    total_market_value = 0.0
    for p in pos_rows:
        price_row = query_db(
            "SELECT close FROM price_history WHERE symbol = ? ORDER BY timestamp DESC LIMIT 1",
            (p['symbol'],),
            one=True,
        )
        if price_row and p['avg_entry']:
            total_unrealized_pnl += p['quantity'] * (price_row['close'] - p['avg_entry'])
            total_market_value += p['quantity'] * price_row['close']

    # Equity = CASH balance + full market value of all open positions
    # (NOT balance + unrealized_pnl — that would omit the cost basis)
    equity = balance_usdt + total_market_value

    return {
        "summary": {
            "balance_usdt": balance_usdt,
            "equity": equity,
            "realized_pnl": realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
        },
        "focus_symbol": _focus_symbol,
        "sources": ["simulator"],
        "tick_count": _tick_count,
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# SSE — live candle stream
# ---------------------------------------------------------------------------

@app.get("/api/stream/candles", include_in_schema=True)
async def stream_candles():
    """Server-Sent Events stream of live PriceEvent candles from Kafka.

    Clients subscribe once; each Kafka message is broadcast as an SSE frame::

        data: {"symbol": "BTC/USDT", "price": 40123.45, ...}

    """
    queue: asyncio.Queue = asyncio.Queue(maxsize=200)
    _sse_clients.append(queue)

    async def generate():
        try:
            while True:
                payload = await queue.get()
                yield f"data: {payload}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            try:
                _sse_clients.remove(queue)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
