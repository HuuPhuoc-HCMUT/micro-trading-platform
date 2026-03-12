import sqlite3
import logging
from datetime import datetime
from models.order import Order 

logger = logging.getLogger(__name__)

# File database sẽ tự động được tạo ra tại thư mục gốc của project
DB_PATH = "trading_platform.db"

def init_db():
    """Khởi tạo file database SQLite và tạo các bảng nếu chưa có."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Bảng Orders (Lưu lịch sử mua bán)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT,
                side TEXT,
                price REAL,
                quantity REAL,
                status TEXT
            )
        ''')
        
        # Bảng Balance (Lưu vết biến động số dư và PnL)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                balance_usdt REAL,
                realized_pnl REAL
            )
        ''')

        # Bảng Price History (Lưu nến OHLCV)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_history (
                timestamp TEXT PRIMARY KEY,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL
            )
        ''')
        
        conn.commit()
        logger.info("🗄️ Database (SQLite) đã được khởi tạo thành công. Sẵn sàng lưu sổ sách!")

def save_order(order: Order):
    """Ghi một lệnh vào Database."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        
        # Xử lý timestamp để đảm bảo lưu dưới dạng chuỗi ISO (dễ đọc)
        ts = order.timestamp.isoformat() if isinstance(order.timestamp, datetime) else order.timestamp
        
        cursor.execute('''
            INSERT INTO orders (timestamp, symbol, side, price, quantity, status)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ts, order.symbol, order.side, order.price, order.quantity, order.status))
        conn.commit()

def save_balance(balance_usdt: float, realized_pnl: float):
    """Ghi lại số dư mới nhất mỗi khi có biến động."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO balance_history (timestamp, balance_usdt, realized_pnl)
            VALUES (?, ?, ?)
        ''', (datetime.now().isoformat(), balance_usdt, realized_pnl))
        conn.commit()

def save_candle(event):
    """Lưu 1 cây nến vào DB. Dùng REPLACE để lỡ có trùng timestamp thì cập nhật luôn."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        ts = event.timestamp.isoformat() if isinstance(event.timestamp, datetime) else event.timestamp
        
        # Lấy OHLC, nếu không có (chưa gộp) thì lấy mặc định là giá close
        open_p = getattr(event, 'open', event.price) or event.price
        high_p = getattr(event, 'high', event.price) or event.price
        low_p = getattr(event, 'low', event.price) or event.price
        
        cursor.execute('''
            INSERT OR REPLACE INTO price_history (timestamp, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ts, open_p, high_p, low_p, event.price, event.volume))
        conn.commit()