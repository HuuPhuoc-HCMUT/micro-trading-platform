from data_source.normalizer import fetch_json, normalize
from models.price_event import PriceEvent

BASE_URL = "https://api.kraken.com/0/public"

SYMBOL_MAP = {
    "BTC/USDT": "XBTUSDT",
    "ETH/USDT": "ETHUSDT",
}


def fetch_ticker(symbol: str = "BTC/USDT") -> PriceEvent:
    """Fetch latest ticker from Kraken and return a normalized PriceEvent."""
    kraken_symbol = SYMBOL_MAP.get(symbol, symbol.replace("/", ""))
    data = fetch_json(f"{BASE_URL}/Ticker", params={"pair": kraken_symbol})

    if data.get("error"):
        raise ValueError(f"Kraken API error: {data['error']}")

    pair_key = next(iter(data["result"]))
    ticker = data["result"][pair_key]

    return normalize({
        "symbol": symbol,
        "price": ticker["c"][0],
        "volume": ticker["v"][1],
        "timestamp": None,
        "source": "kraken",
    })
