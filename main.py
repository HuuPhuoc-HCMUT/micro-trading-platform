from __future__ import annotations

import argparse
import logging
import time
import socket
import json
from datetime import datetime

# Import CEP Detectors
from cep_engine.moving_average import MovingAverageDetector
from cep_engine.spike_detector import SpikeDetector
from cep_engine.volume_anomaly import VolumeAnomalyDetector

# Import Trading Engine
from trading_engine.order_manager import OrderManager
from trading_engine.strategy import RuleBasedStrategy

from data_simulator.price_stream import generate_price_stream
# Chú ý: Import PriceEvent của bạn ở đây (giả sử từ models)
from models.price_event import PriceEvent 

from data_source.binance_client import stream_trades as binance_stream
from data_source.coinbase_client import stream_trades as coinbase_stream
from data_source.kraken_client import stream_trades as kraken_stream
from data_source.time_aggregator import TimeAggregator # <--- THÊM DÒNG NÀY Ở ĐẦU FILE

from database.db import init_db, save_candle

from logger_config import setup_logging

# Kafka streaming layer
import os
from streaming.kafka_producer import KafkaEventProducer
from streaming.kafka_consumer import KafkaEventConsumer
from streaming.topics import PRICE_EVENTS_TOPIC, ALERTS_TOPIC, ORDERS_TOPIC
from streaming.serializers import (
    serialize_price_event,
    deserialize_price_event,
    serialize_alert,
    deserialize_alert,
    serialize_order,
    deserialize_order,
)

setup_logging()
logger = logging.getLogger(__name__)

EXCHANGE_STREAMS = {
    "binance": binance_stream,
    "coinbase": coinbase_stream,
    "kraken": kraken_stream,
}

UDP_IP = "127.0.0.1"
UDP_PORT = 9999

def real_data_stream(symbol: str, source: str):
    """Mở luồng WebSocket trực tiếp từ sàn."""
    if source not in EXCHANGE_STREAMS:
        raise ValueError(f"Unknown source: {source}")
    
    # Chỉ cần gọi hàm, nó sẽ tự động yield data liên tục không cần sleep
    return EXCHANGE_STREAMS[source](symbol)

def serialize_event(event: PriceEvent) -> bytes:
    """Biến PriceEvent thành chuỗi JSON (bytes) để gửi qua mạng."""
    # Nếu PriceEvent là Pydantic model, bạn có thể dùng event.model_dump_json()
    data = {
        "symbol": event.symbol,
        "price": event.price,
        "volume": event.volume,
        # Xử lý timestamp thành string chuẩn ISO
        "timestamp": event.timestamp.isoformat() if isinstance(event.timestamp, datetime) else event.timestamp,
        "source": getattr(event, "source", "simulator")
    }
    return json.dumps(data).encode('utf-8')

def deserialize_event(data_bytes: bytes) -> PriceEvent:
    """Biến chuỗi JSON (bytes) nhận từ mạng trở lại thành object PriceEvent."""
    data = json.loads(data_bytes.decode('utf-8'))
    # Đảm bảo parse lại datetime
    if "timestamp" in data and isinstance(data["timestamp"], str):
        data["timestamp"] = datetime.fromisoformat(data["timestamp"])
    return PriceEvent(**data)

def run_publisher(stream):
    """TERMINAL 1: Lấy Data, GOM NẾN (Aggregator), rồi mới bắn qua mạng"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    
    # Khởi tạo bộ gom nến 1 giây
    aggregator = TimeAggregator(interval_seconds=1.0) 
    
    candle_count = 0
    logger.info(f"🚀 PUBLISHER is running. Aggregating to 1s candles -> UDP {UDP_IP}:{UDP_PORT}")
    
    try:
        for raw_event in stream:
            # 1. Ném tick thô vào Aggregator
            aggregated_event = aggregator.process(raw_event)
            
            # 2. Chỉ khi nào nến đóng (trả về event) thì mới in log và gửi đi
            if aggregated_event:
                candle_count += 1
                
                # In ra log siêu đẹp để bạn thấy nó đã gom bao nhiêu lệnh
                logger.info(
                    "[CANDLE #%d] %s | Close: $%.2f | Vol: %.4f | Ticks gom: %d",
                    candle_count, 
                    aggregated_event.symbol, 
                    aggregated_event.price, 
                    aggregated_event.volume,
                    getattr(aggregated_event, "ticks_count", 0)
                )
                
                # 3. Gửi nến đã gom qua cho Terminal 2 (Subscriber)
                sock.sendto(serialize_event(aggregated_event), (UDP_IP, UDP_PORT))
                
    except KeyboardInterrupt:
        logger.info("Publisher stopped.")

def run_subscriber():
    """TERMINAL 2: Lắng nghe mạng, chạy CEP và Strategy"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    logger.info(f"🎧 SUBSCRIBER is listening on UDP {UDP_IP}:{UDP_PORT}...")
    
    init_db()

    # 1. Khởi tạo CEP Engine (Cả 3 bộ lọc)
    ma_detector = MovingAverageDetector(short_window=15, long_window=60)
    spike_detector = SpikeDetector(threshold_percent=2.0, window_size=5)
    volume_detector = VolumeAnomalyDetector(window_size=10, multiplier=3.0)

    # 2. Khởi tạo Trading Engine
    order_manager = OrderManager(initial_balance=10000.0)
    strategy = RuleBasedStrategy(order_manager)

    try:
        while True:
            # Nhận data từ Publisher
            data, addr = sock.recvfrom(4096)
            event = deserialize_event(data)

            save_candle(event)
            
            # Quét qua các máy dò tín hiệu
            alerts = []
            
            ma_alert = ma_detector.process(event)
            if ma_alert: alerts.append(ma_alert)
                
            spike_alert = spike_detector.process(event)
            if spike_alert: alerts.append(spike_alert)
                
            vol_alert = volume_detector.process(event)
            if vol_alert: alerts.append(vol_alert)

            # Đưa toàn bộ alerts vào Bộ não Strategy xử lý
            strategy.execute(alerts, event)

    except KeyboardInterrupt:
        logger.info("Subscriber stopped. Final Portfolio:")
        print(order_manager.get_portfolio_summary())

# Read broker address from environment so containers can override it.
KAFKA_BOOTSTRAP_SERVERS: str = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")


def run_kafka_publisher(stream) -> None:
    """Kafka publisher: read from a data stream and publish PriceEvents.

    Aggregates raw ticks into 1-second candles (via :class:`TimeAggregator`)
    then serialises each closed candle and sends it to the
    ``market.price_events`` Kafka topic, keyed by symbol.

    Args:
        stream: An iterable of :class:`PriceEvent` objects (simulator or
            real exchange client).
    """
    aggregator = TimeAggregator(interval_seconds=1.0)
    candle_count = 0

    logger.info(
        "🚀 KAFKA PUBLISHER starting — broker=%s topic=%s",
        KAFKA_BOOTSTRAP_SERVERS, PRICE_EVENTS_TOPIC,
    )

    with KafkaEventProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS) as producer:
        try:
            for raw_event in stream:
                aggregated_event = aggregator.process(raw_event)
                if aggregated_event:
                    candle_count += 1
                    logger.info(
                        "[CANDLE #%d] %s | Close: $%.2f | Vol: %.4f | Ticks: %d",
                        candle_count,
                        aggregated_event.symbol,
                        aggregated_event.price,
                        aggregated_event.volume,
                        getattr(aggregated_event, "ticks_count", 0),
                    )
                    producer.send(
                        PRICE_EVENTS_TOPIC,
                        value=serialize_price_event(aggregated_event),
                        key=aggregated_event.symbol,
                    )
        except KeyboardInterrupt:
            logger.info("Kafka publisher stopped.")


def run_kafka_cep_worker() -> None:
    """Kafka CEP worker: consume PriceEvents and publish Alerts.

    Subscribes to ``market.price_events``, runs all three CEP detectors
    (Moving Average, Spike, Volume Anomaly) on each event, then publishes
    any resulting :class:`Alert` objects to ``market.alerts``.
    """
    logger.info(
        "🔬 KAFKA CEP WORKER starting — broker=%s",
        KAFKA_BOOTSTRAP_SERVERS,
    )

    ma_detector = MovingAverageDetector(short_window=15, long_window=60)
    spike_detector = SpikeDetector(threshold_percent=2.0, window_size=5)
    volume_detector = VolumeAnomalyDetector(window_size=10, multiplier=3.0)

    consumer = KafkaEventConsumer(
        topics=[PRICE_EVENTS_TOPIC],
        group_id="cep-worker",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="latest",
    )

    with consumer, KafkaEventProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS) as producer:
        for raw in consumer.consume():
            try:
                event = deserialize_price_event(raw)
            except Exception as exc:
                logger.error("Failed to deserialise PriceEvent: %s", exc)
                continue

            detectors = [ma_detector, spike_detector, volume_detector]
            for detector in detectors:
                try:
                    alert = detector.process(event)
                    if alert:
                        logger.warning(
                            ">>> ALERT [%s] %s | %s",
                            alert.signal_type, alert.symbol, alert.message,
                        )
                        producer.send(
                            ALERTS_TOPIC,
                            value=serialize_alert(alert),
                            key=alert.symbol,
                        )
                except Exception as exc:
                    logger.error(
                        "CEP detector %s raised an error: %s",
                        type(detector).__name__, exc,
                    )


def run_kafka_strategy_worker() -> None:
    """Kafka strategy worker: consume Alerts and publish Orders.

    Subscribes to ``market.alerts``, buffers alerts per symbol within a
    short accumulation window, then passes them to :class:`RuleBasedStrategy`
    which may produce paper-trade orders that are saved to the DB and
    published to ``market.orders``.

    The worker also maintains its own in-memory price cache so the strategy
    can calculate P&L without an extra round-trip to the database.
    """
    logger.info(
        "📈 KAFKA STRATEGY WORKER starting — broker=%s",
        KAFKA_BOOTSTRAP_SERVERS,
    )

    init_db()

    order_manager = OrderManager(initial_balance=10000.0)
    strategy = RuleBasedStrategy(order_manager)

    # Price cache: symbol → latest price (for unrealised P&L)
    price_cache: dict[str, float] = {}

    # Alert accumulation buffer: symbol → list of Alerts received this cycle
    alert_buffer: dict[str, list] = {}

    consumer = KafkaEventConsumer(
        topics=[ALERTS_TOPIC],
        group_id="strategy-worker",
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        auto_offset_reset="latest",
    )

    with consumer, KafkaEventProducer(bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS) as producer:
        for raw in consumer.consume():
            try:
                alert = deserialize_alert(raw)
            except Exception as exc:
                logger.error("Failed to deserialise Alert: %s", exc)
                continue

            symbol = alert.symbol
            alert_buffer.setdefault(symbol, []).append(alert)

            # Build a synthetic PriceEvent from the alert's trigger price
            # (extracted from the message when available, otherwise cached).
            # The strategy only needs event.symbol and event.price.
            cached_price = price_cache.get(symbol, 0.0)

            # Try to parse price from alert message ("... @ $42000.00")
            try:
                import re
                match = re.search(r"\$([0-9]+(?:\.[0-9]+)?)", alert.message)
                if match:
                    cached_price = float(match.group(1))
                    price_cache[symbol] = cached_price
            except Exception:
                pass

            if cached_price == 0.0:
                # Not enough info yet — wait for a price-bearing alert
                continue

            from models.price_event import PriceEvent as _PE
            synthetic_event = _PE(
                symbol=symbol,
                price=cached_price,
                volume=0.0,
                timestamp=alert.triggered_at,
                source="kafka-strategy",
            )

            # Run strategy with accumulated alerts for this symbol
            pending_alerts = alert_buffer.pop(symbol, [])
            strategy.execute(pending_alerts, synthetic_event)

            # Publish the latest filled order (if any) to market.orders
            if order_manager.order_history:
                latest_order = order_manager.order_history[-1]
                if latest_order.status == "FILLED":
                    producer.send(
                        ORDERS_TOPIC,
                        value=serialize_order(latest_order),
                        key=symbol,
                    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Micro Trading Platform")
    
    # 1. Cấu hình MODE (Chế độ chạy)
    parser.add_argument(
        "--mode",
        type=str,
        choices=[
            "publisher", "subscriber", "standalone",
            "kafka-publisher", "kafka-cep", "kafka-strategy",
        ],
        default="standalone",
        help=(
            "Run mode: publisher/subscriber (UDP, Phase 1) | "
            "kafka-publisher / kafka-cep / kafka-strategy (Kafka, Phase 2)"
        ),
    )
    
    # 2. Cấu hình SOURCE (Nguồn dữ liệu - Chỉ nhận 1 giá trị)
    parser.add_argument(
        "--source", 
        type=str,
        choices=["simulator", "binance", "coinbase", "kraken"],
        default="simulator",
        help="Chọn MỘT nguồn dữ liệu (VD: binance, kraken, simulator)"
    )
    
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pair")
    parser.add_argument("--interval", type=float, default=1.0, help="Seconds between ticks")
    args = parser.parse_args()

    if args.mode == "subscriber":
        # Subscriber không cần khởi tạo luồng dữ liệu, chỉ cần mở port lắng nghe
        run_subscriber()
        return

    if args.mode == "kafka-cep":
        run_kafka_cep_worker()
        return

    if args.mode == "kafka-strategy":
        run_kafka_strategy_worker()
        return

    # Khởi tạo Data Stream (dùng cho Publisher)
    if args.source == "simulator":
        stream = generate_price_stream(
            symbol=args.symbol, start_price=40000.0, interval=args.interval,
        )
    else:
        # Xóa args.interval ở đây đi
        stream = real_data_stream(args.symbol, args.source)

    if args.mode == "publisher":
        run_publisher(stream)
    elif args.mode == "kafka-publisher":
        run_kafka_publisher(stream)
    else:
        # Chế độ Standalone
        logger.info("Chạy chế độ STANDALONE (Chưa hỗ trợ gộp trong bản update này, vui lòng dùng terminal riêng)")
        pass

if __name__ == "__main__":
    main()