from data_source.normalizer import fetch_json, normalize
from models.price_event import PriceEvent

BASE_URL = "https://api.exchange.coinbase.com"

SYMBOL_MAP = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
}


def fetch_ticker(symbol: str = "BTC/USDT") -> PriceEvent:
    """Fetch latest ticker from Coinbase and return a normalized PriceEvent."""
    coinbase_symbol = SYMBOL_MAP.get(symbol, symbol.replace("/", "-"))
    data = fetch_json(f"{BASE_URL}/products/{coinbase_symbol}/ticker")

    return normalize({
        "symbol": symbol,
        "price": data["price"],
        "volume": data["volume"],
        "timestamp": data["time"],
        "source": "coinbase",
    })
