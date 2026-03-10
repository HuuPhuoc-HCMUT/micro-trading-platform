from data_source.normalizer import fetch_json, normalize
from models.price_event import PriceEvent

BASE_URL = "https://api.binance.com/api/v3"

SYMBOL_MAP = {
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
}


def fetch_ticker(symbol: str = "BTC/USDT") -> PriceEvent:
    """Fetch latest ticker from Binance and return a normalized PriceEvent."""
    binance_symbol = SYMBOL_MAP.get(symbol, symbol.replace("/", ""))
    data = fetch_json(f"{BASE_URL}/ticker/24hr", params={"symbol": binance_symbol})

    return normalize({
        "symbol": symbol,
        "price": data["lastPrice"],
        "volume": data["volume"],
        "timestamp": int(data["closeTime"]),
        "source": "binance",
    })
