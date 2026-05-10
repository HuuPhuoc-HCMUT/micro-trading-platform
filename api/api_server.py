from datetime import datetime
import sqlite3
from pathlib import Path
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app = FastAPI(
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

@app.get("/api/portfolio")
def get_portfolio():
    """Lấy số dư tài khoản và PnL mới nhất."""
    latest_balance = query_db(
        "SELECT * FROM balance_history ORDER BY id DESC LIMIT 1",
        one=True
    )

    if latest_balance:
        return latest_balance

    return {
        "balance_usdt": 10000.0,
        "realized_pnl": 0.0,
        "timestamp": "Chưa có giao dịch"
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
def get_signals(limit: int = 8):
    """Trả về danh sách chuỗi tín hiệu CEP để hiển thị trên dashboard."""
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

    return {
        "symbol": target,
        "source": "simulator",
        "close": close,
        "change": change,
        "trend_arrow": trend_arrow,
        "trend_label": trend_label,
        "edge": 0.0,
        "confidence": 0.0,
        "unrealized": 0.0,
        "position_qty": 0.0,
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

    # Số dư mới nhất
    balance_row = query_db(
        "SELECT balance_usdt, realized_pnl FROM balance_history ORDER BY id DESC LIMIT 1",
        one=True
    )
    balance_usdt = balance_row['balance_usdt'] if balance_row else 10000.0
    realized_pnl = balance_row['realized_pnl'] if balance_row else 0.0

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

    rows = []
    for r in symbol_rows:
        change = r.get('change') or 0.0
        if change > 0.001:
            trend_arrow, trend_label = "↑", "Up"
        elif change < -0.001:
            trend_arrow, trend_label = "↓", "Down"
        else:
            trend_arrow, trend_label = "→", "Flat"
        rows.append({
            "symbol": r['symbol'],
            "source": "simulator",
            "active_sources": ["simulator"],
            "close": r['close'],
            "change": change,
            "trend_arrow": trend_arrow,
            "trend_label": trend_label,
        })

    # Đặt focus_symbol mặc định nếu chưa có
    if not _focus_symbol and rows:
        _focus_symbol = rows[0]['symbol']

    return {
        "summary": {
            "balance_usdt": balance_usdt,
            "equity": balance_usdt,
            "realized_pnl": realized_pnl,
            "total_unrealized_pnl": 0.0,
        },
        "focus_symbol": _focus_symbol,
        "sources": ["simulator"],
        "tick_count": _tick_count,
        "rows": rows,
    }
