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
                status TEXT,
                trade_pnl REAL
            )
        ''')
        # Migration: thêm cột trade_pnl nếu chưa có (DB cũ)
        try:
            cursor.execute("ALTER TABLE orders ADD COLUMN trade_pnl REAL")
        except sqlite3.OperationalError:
            pass  # Cột đã tồn tại
        
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
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                symbol TEXT DEFAULT '',
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                ticks_count INTEGER DEFAULT 1
            )
        ''')
        # Migration: thêm cột symbol và ticks_count nếu chưa có (DB cũ)
        for col_sql in [
            "ALTER TABLE price_history ADD COLUMN symbol TEXT DEFAULT ''",
            "ALTER TABLE price_history ADD COLUMN ticks_count INTEGER DEFAULT 1",
        ]:
            try:
                cursor.execute(col_sql)
            except sqlite3.OperationalError:
                pass  # Cột đã tồn tại

        # Bảng Alerts (Lưu tín hiệu CEP)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                triggered_at TEXT,
                signal_type TEXT,
                symbol TEXT,
                severity TEXT,
                message TEXT,
                price REAL
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
    """Lưu 1 cây nến vào DB."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        ts = event.timestamp.isoformat() if isinstance(event.timestamp, datetime) else event.timestamp
        
        # Lấy OHLC, nếu không có (chưa gộp) thì lấy mặc định là giá close
        open_p = getattr(event, 'open', event.price) or event.price
        high_p = getattr(event, 'high', event.price) or event.price
        low_p = getattr(event, 'low', event.price) or event.price
        
        cursor.execute('''
            INSERT INTO price_history (timestamp, symbol, open, high, low, close, volume, ticks_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ts, event.symbol, open_p, high_p, low_p, event.price, event.volume, 1))
        conn.commit()

def save_alert(alert) -> None:
    """Ghi một tín hiệu CEP vào Database."""
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        ts = alert.triggered_at.isoformat() if isinstance(alert.triggered_at, datetime) else alert.triggered_at
        cursor.execute('''
            INSERT INTO alerts (triggered_at, signal_type, symbol, severity, message, price)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (ts, alert.signal_type, alert.symbol, alert.severity, alert.message, alert.price))
        conn.commit()