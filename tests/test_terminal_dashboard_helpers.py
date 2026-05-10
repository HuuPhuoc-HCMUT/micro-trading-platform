from datetime import datetime, timezone

from terminal_dashboard import parse_sources, parse_symbols, render_candle_strip, sparkline, trend_from_candles


def test_parse_symbols() -> None:
    result = parse_symbols(["BTC/USDT=40000", "ETH/USDT=2500"])
    assert result["BTC/USDT"] == 40000.0
    assert result["ETH/USDT"] == 2500.0


def test_parse_sources_supports_multiple_real_exchanges() -> None:
    result = parse_sources("binance+coinbase+kraken")
    assert result == ["binance", "coinbase", "kraken"]


def test_sparkline_returns_text() -> None:
    line = sparkline([1, 2, 3, 2, 5, 8, 13], width=5)
    assert len(line) == 5


def test_trend_from_candles_detects_uptrend() -> None:
    candles = [
        {"timestamp": datetime.now(timezone.utc), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.0, "volume": 5.0, "ticks": 4},
        {"timestamp": datetime.now(timezone.utc), "open": 100.0, "high": 103.0, "low": 99.9, "close": 102.0, "volume": 8.0, "ticks": 7},
    ]
    trend = trend_from_candles(candles)
    assert trend["label"] == "UP"
    assert trend["change"] > 0


def test_render_candle_strip_returns_symbols() -> None:
    candles = [
        {"timestamp": datetime.now(timezone.utc), "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 5.0, "ticks": 4},
        {"timestamp": datetime.now(timezone.utc), "open": 100.5, "high": 101.2, "low": 99.8, "close": 99.9, "volume": 6.0, "ticks": 5},
    ]
    strip = render_candle_strip(candles, width=2)
    assert strip
