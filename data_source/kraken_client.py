import json
import websocket
from data_source.normalizer import normalize

SYMBOL_MAP = {
    "BTC/USDT": "XBT/USD",
    "ETH/USDT": "ETH/USD",
    "SOL/USDT": "SOL/USD",
    "ADA/USDT": "ADA/USD",
    "DOGE/USDT": "DOGE/USD",
}

def stream_trades(symbol: str = "BTC/USDT"):
    kraken_symbol = SYMBOL_MAP.get(symbol, symbol.replace("/", "-"))
    url = "wss://ws.kraken.com"

    ws = websocket.create_connection(url)
    
    subscribe_msg = {
        "event": "subscribe",
        "pair": [kraken_symbol],
        "subscription": {"name": "trade"}
    }
    ws.send(json.dumps(subscribe_msg))

    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            
            # Kraken gửi dữ liệu trade dạng List: [channelID, [[price, volume, time, side, orderType, misc]], channelName, pair]
            if isinstance(data, list) and len(data) >= 4 and data[2] == "trade":
                trades = data[1]
                for trade in trades:
                    yield normalize({
                        "symbol": symbol,
                        "price": trade[0],
                        "volume": trade[1], # Khối lượng thực của lệnh
                        "timestamp": float(trade[2]),
                        "source": "kraken",
                    })
    finally:
        ws.close()


def iter_kraken_trades(symbols: list[str]):
    """Open a Kraken stream and yield normalized trades for multiple pairs."""
    if not symbols:
        return

    pair_map = {
        SYMBOL_MAP.get(symbol, symbol.replace("/", "/")): symbol
        for symbol in symbols
    }
    url = "wss://ws.kraken.com"
    ws = websocket.create_connection(url)

    subscribe_msg = {
        "event": "subscribe",
        "pair": list(pair_map.keys()),
        "subscription": {"name": "trade"},
    }
    ws.send(json.dumps(subscribe_msg))

    try:
        while True:
            message = ws.recv()
            data = json.loads(message)
            if isinstance(data, list) and len(data) >= 4 and data[2] == "trade":
                pair = data[3]
                symbol = pair_map.get(pair, pair.replace("/", "/"))
                trades = data[1]
                for trade in trades:
                    yield normalize({
                        "symbol": symbol,
                        "price": trade[0],
                        "volume": trade[1],
                        "timestamp": float(trade[2]) * 1000,
                        "source": "kraken",
                    })
    finally:
        ws.close()
