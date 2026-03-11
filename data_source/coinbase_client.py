import json
import websocket
from data_source.normalizer import normalize
from models.price_event import PriceEvent

SYMBOL_MAP = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
}

def stream_trades(symbol: str = "BTC/USDT"):
    coinbase_symbol = SYMBOL_MAP.get(symbol, symbol.replace("/", "-"))
    url = "wss://ws-feed.exchange.coinbase.com"

    ws = websocket.create_connection(url)
    
    # Gửi yêu cầu đăng ký kênh "matches" (trade)
    subscribe_msg = {
        "type": "subscribe",
        "product_ids": [coinbase_symbol],
        "channels": ["matches"]
    }
    ws.send(json.dumps(subscribe_msg))

    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            
            # Chỉ xử lý các message dạng 'match' (khớp lệnh)
            if data.get("type") == "match":
                yield normalize({
                    "symbol": symbol,
                    "price": data["price"],
                    "volume": data["size"], # Khối lượng thực của lệnh
                    "timestamp": data["time"],
                    "source": "coinbase",
                })
    finally:
        ws.close()