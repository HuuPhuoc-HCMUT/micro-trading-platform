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

from logger_config import setup_logging

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
    """TERMINAL 1: Chỉ lấy Data và bắn qua mạng (Cổng 9999)"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    tick_count = 0
    logger.info(f"🚀 PUBLISHER is running. Sending data to UDP {UDP_IP}:{UDP_PORT}")
    
    try:
        for event in stream:
            tick_count += 1
            logger.info(
                "[PUBLISHER TICK #%d] %s price=%.2f vol=%.4f src=%s",
                tick_count, event.symbol, event.price, event.volume, getattr(event, "source", "simulator")
            )
            # Gửi data đi
            sock.sendto(serialize_event(event), (UDP_IP, UDP_PORT))
    except KeyboardInterrupt:
        logger.info("Publisher stopped.")

def run_subscriber():
    """TERMINAL 2: Lắng nghe mạng, chạy CEP và Strategy"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((UDP_IP, UDP_PORT))
    
    logger.info(f"🎧 SUBSCRIBER is listening on UDP {UDP_IP}:{UDP_PORT}...")
    
    # 1. Khởi tạo CEP Engine (Cả 3 bộ lọc)
    ma_detector = MovingAverageDetector(short_window=500, long_window=2000)
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

def main() -> None:
    parser = argparse.ArgumentParser(description="Micro Trading Platform")
    
    # 1. Cấu hình MODE (Chế độ chạy)
    parser.add_argument(
        "--mode",
        type=str,
        choices=["publisher", "subscriber", "standalone"],
        default="standalone",
        help="Chế độ chạy: publisher (Terminal 1), subscriber (Terminal 2), hoặc standalone",
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
    else:
        # Chế độ Standalone
        logger.info("Chạy chế độ STANDALONE (Chưa hỗ trợ gộp trong bản update này, vui lòng dùng terminal riêng)")
        pass

if __name__ == "__main__":
    main()