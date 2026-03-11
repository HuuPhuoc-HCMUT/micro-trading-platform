import json
import websocket
from data_source.normalizer import normalize
from models.price_event import PriceEvent

# Thêm stream_trades thay thế fetch_ticker
def stream_trades(symbol: str = "BTC/USDT"):
    """Mở WebSocket tới Binance và yield từng sự kiện khớp lệnh (Trade)."""
    # Binance websocket yêu cầu symbol viết thường (vd: btcusdt)
    binance_symbol = symbol.replace("/", "").lower()
    url = f"wss://stream.binance.com:9443/ws/{binance_symbol}@trade"

    # Tạo kết nối WebSocket
    ws = websocket.create_connection(url)
    
    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            
            # 'p': Price, 'q': Quantity (Khối lượng của lệnh VỪA KHỚP), 'T': Timestamp
            yield normalize({
                "symbol": symbol,
                "price": data["p"],
                "volume": data["q"], 
                "timestamp": data["T"],
                "source": "binance",
            })
    finally:
        ws.close()