import json
import websocket
from data_source.normalizer import normalize

def _to_binance_symbol(symbol: str) -> str:
    return symbol.replace("/", "").lower()


def stream_trades(symbol: str = "BTC/USDT"):
    """Mở WebSocket tới Binance và yield từng sự kiện khớp lệnh (Trade)."""
    binance_symbol = _to_binance_symbol(symbol)
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


def iter_binance_trades(symbols: list[str]):
    """Open a multiplex Binance stream and yield normalized trade events."""
    if not symbols:
        return

    stream_path = "/".join(f"{_to_binance_symbol(symbol)}@trade" for symbol in symbols)
    url = f"wss://stream.binance.com:9443/stream?streams={stream_path}"
    symbol_map = {_to_binance_symbol(symbol): symbol for symbol in symbols}
    ws = websocket.create_connection(url)

    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            payload = data.get("data", data)
            stream_name = str(data.get("stream", "")).split("@", 1)[0]
            symbol = symbol_map.get(stream_name, payload.get("s", "UNKNOWN"))
            yield normalize({
                "symbol": symbol,
                "price": payload["p"],
                "volume": payload["q"],
                "timestamp": payload["T"],
                "source": "binance",
            })
    finally:
        ws.close()
