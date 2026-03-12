from datetime import datetime
import sqlite3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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
    return {"status": "🚀 API Server đang chạy mượt mà!"}

@app.get("/api/portfolio")
def get_portfolio():
    """Lấy số dư tài khoản và PnL mới nhất."""
    # Tìm dòng cuối cùng trong bảng balance_history
    latest_balance = query_db(
        "SELECT * FROM balance_history ORDER BY id DESC LIMIT 1", 
        one=True
    )
    
    if latest_balance:
        return latest_balance
    
    # Nếu chưa có giao dịch nào, trả về số dư mặc định
    return {
        "balance_usdt": 10000.0,
        "realized_pnl": 0.0,
        "timestamp": "Chưa có giao dịch"
    }

@app.get("/api/orders")
def get_orders(limit: int = 50):
    """Lấy lịch sử 50 lệnh giao dịch gần nhất."""
    orders = query_db(
        "SELECT * FROM orders ORDER BY id DESC LIMIT ?", 
        (limit,)
    )
    return orders

@app.get("/api/candles")
def get_candles(limit: int = 100):
    """Lấy dữ liệu nến OHLC để vẽ biểu đồ TradingView."""
    # Lấy 100 nến gần nhất, nhưng phải sắp xếp lại theo thời gian tăng dần (cũ -> mới) để vẽ chart
    candles = query_db(
        "SELECT * FROM (SELECT * FROM price_history ORDER BY timestamp DESC LIMIT ?) ORDER BY timestamp ASC", 
        (limit,)
    )
    
    # Format lại dữ liệu cho đúng chuẩn mà thư viện Lightweight Charts yêu cầu (time, open, high, low, close)
    formatted_candles = []
    for c in candles:
        # Chuyển ISO string thành Unix Timestamp (giây)
        dt = datetime.fromisoformat(c['timestamp'].replace("Z", "+00:00"))
        formatted_candles.append({
            "time": int(dt.timestamp()), 
            "open": c['open'],
            "high": c['high'],
            "low": c['low'],
            "close": c['close']
        })
        
    return formatted_candles