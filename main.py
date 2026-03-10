import logging

from cep_engine.moving_average import MovingAverageDetector
from data_simulator.price_stream import generate_price_stream
from logger_config import setup_logging

setup_logging()
logger = logging.getLogger(__name__)


def main() -> None:
    logger.info("Starting micro-trading-platform")

    stream = generate_price_stream(symbol="BTC/USDT", start_price=40000.0, interval=0.3)
    ma_detector = MovingAverageDetector(short_window=5, long_window=20)

    tick_count = 0

    try:
        for event in stream:
            tick_count += 1
            logger.info(
                "[TICK #%d] %s  price=%.2f  volume=%.4f",
                tick_count, event.symbol, event.price, event.volume,
            )

            # CEP: check for moving average crossover
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
