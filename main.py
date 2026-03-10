import argparse
import logging
import time

from cep_engine.moving_average import MovingAverageDetector
from data_simulator.price_stream import generate_price_stream
from data_source.binance_client import fetch_ticker as binance_fetch
from data_source.coinbase_client import fetch_ticker as coinbase_fetch
from data_source.kraken_client import fetch_ticker as kraken_fetch
from logger_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)

EXCHANGE_FETCHERS = {
    "binance": binance_fetch,
    "coinbase": coinbase_fetch,
    "kraken": kraken_fetch,
}


def real_data_stream(symbol: str, sources: list[str], interval: float):
    """Fetch live tickers from multiple exchanges in a round-robin loop."""
    fetchers = []
    for src in sources:
        if src not in EXCHANGE_FETCHERS:
            raise ValueError(f"Unknown source: {src}")
        fetchers.append((src, EXCHANGE_FETCHERS[src]))

    while True:
        for name, fetch in fetchers:
            try:
                yield fetch(symbol)
            except Exception:
                logger.error("Failed to fetch from %s", name, exc_info=True)
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Micro Trading Platform")
    parser.add_argument(
        "--source",
        nargs="+",
        choices=["simulator", "binance", "coinbase", "kraken"],
        default=["simulator"],
        help="Data source(s) — can specify multiple (default: simulator)",
    )
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between ticks")
    args = parser.parse_args()

    logger.info(
        "Starting micro-trading-platform [sources=%s, symbol=%s]",
        args.source, args.symbol,
    )

    if args.source == ["simulator"]:
        stream = generate_price_stream(
            symbol=args.symbol, start_price=40000.0, interval=args.interval,
        )
    else:
        sources = [s for s in args.source if s != "simulator"]
        stream = real_data_stream(args.symbol, sources, args.interval)

    ma_detector = MovingAverageDetector(short_window=5, long_window=20)
    tick_count = 0

    try:
        for event in stream:
            tick_count += 1
            logger.info(
                "[TICK #%d] %s  price=%.2f  volume=%.4f  source=%s",
                tick_count, event.symbol, event.price, event.volume, event.source,
            )

            alert = ma_detector.process(event)
            if alert:
                logger.warning(
                    ">>> ALERT [%s] %s | severity=%s | %s",
                    alert.signal_type, alert.symbol, alert.severity, alert.message,
                )
    except KeyboardInterrupt:
        logger.info("Stopped by user after %d ticks", tick_count)


if __name__ == "__main__":
    main()
