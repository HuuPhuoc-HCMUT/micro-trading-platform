import logging
from datetime import datetime
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)

class TimeAggregator:
    """
    Gom các tick data siêu tốc thành các nến (OHLCV) theo khung thời gian.
    Ví dụ: Khung 1 giây, 1 phút...
    """

    def __init__(self, interval_seconds: float = 1.0) -> None:
        self.interval_seconds = interval_seconds
        self.current_window_start: datetime | None = None
        self.prices: list[float] = []
        self.total_volume: float = 0.0
        self.last_symbol: str = ""
        self.last_source: str = ""

    def process(self, event: PriceEvent) -> PriceEvent | None:
        """
        Nhận vào 1 tick. 
        - Nếu nằm trong cửa sổ hiện tại -> Lưu lại vào bộ nhớ đệm, trả về None.
        - Nếu tràn cửa sổ -> Đóng nến cũ, phát ra 1 PriceEvent tổng hợp, và mở nến mới.
        """
        # 1. Khởi tạo nến đầu tiên khi hệ thống mới chạy
        if self.current_window_start is None:
            self.current_window_start = event.timestamp
            self.prices.append(event.price)
            self.total_volume += event.volume
            self.last_symbol = event.symbol
            self.last_source = getattr(event, "source", "unknown")
            return None

        # 2. Tính xem tick hiện tại cách lúc bắt đầu nến bao nhiêu giây
        time_diff = (event.timestamp - self.current_window_start).total_seconds()

        if time_diff < self.interval_seconds:
            # Vẫn nằm trong cùng 1 giây (hoặc khung thời gian bạn cấu hình) -> Gom vào nến
            self.prices.append(event.price)
            self.total_volume += event.volume
            return None
            
        else:
            # 3. ĐÃ SANG GIÂY MỚI -> Đóng nến cũ và xuất xưởng!
            open_price = self.prices[0]
            high_price = max(self.prices)
            low_price = min(self.prices)
            close_price = self.prices[-1]
            ticks_count = len(self.prices)
            
            # Tạo Event tổng hợp (Dùng Close làm giá chính cho thuật toán)
            aggregated_event = PriceEvent(
                symbol=self.last_symbol,
                price=close_price,
                volume=self.total_volume,
                timestamp=self.current_window_start,
                source=f"{self.last_source}_agg"
            )
            
            # (Tùy chọn) Gắn thêm OHLC vào object để sau này in Log hoặc vẽ biểu đồ UI
            aggregated_event.open = open_price
            aggregated_event.high = high_price
            aggregated_event.low = low_price
            aggregated_event.ticks_count = ticks_count

            # 4. Mở nến mới với data của tick hiện tại
            self.current_window_start = event.timestamp
            self.prices = [event.price]
            self.total_volume = event.volume
            self.last_symbol = event.symbol
            self.last_source = getattr(event, "source", "unknown")

            return aggregated_event