import json
import websocket
from data_source.normalizer import normalize

SYMBOL_MAP = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
    "SOL/USDT": "SOL-USD",
    "ADA/USDT": "ADA-USD",
    "DOGE/USDT": "DOGE-USD",
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


def iter_coinbase_trades(symbols: list[str]):
    """Open a Coinbase stream and yield normalized trades for multiple symbols."""
    if not symbols:
        return

    product_map = {
        SYMBOL_MAP.get(symbol, symbol.replace("/", "-")): symbol
        for symbol in symbols
    }
    url = "wss://ws-feed.exchange.coinbase.com"
    ws = websocket.create_connection(url)

    subscribe_msg = {
        "type": "subscribe",
        "product_ids": list(product_map.keys()),
        "channels": ["matches"],
    }
    ws.send(json.dumps(subscribe_msg))

    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            if data.get("type") != "match":
                continue

            product_id = data.get("product_id", "")
            symbol = product_map.get(product_id, product_id.replace("-", "/"))
            yield normalize({
                "symbol": symbol,
                "price": data["price"],
                "volume": data["size"],
                "timestamp": data["time"],
                "source": "coinbase",
            })
    finally:
        ws.close()
