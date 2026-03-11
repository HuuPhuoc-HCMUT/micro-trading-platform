import logging

from models.alert import Alert
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager

logger = logging.getLogger(__name__)

class RuleBasedStrategy:
    """
    Advanced Strategy using Signal Scoring (Confluence) and Volume Confirmation.
    """

    def __init__(self, order_manager: OrderManager) -> None:
        self.order_manager = order_manager
        # Base trading quantities (Khối lượng vào lệnh cơ bản)
        self.base_quantities = {
            "BTC/USDT": 0.05,
            "ETH/USDT": 0.5,
            "SOL/USDT": 5.0
        }

    def execute(self, alerts: list[Alert], event: PriceEvent) -> None:
        """Evaluates alerts using a scoring system to make a final decision."""
        if not alerts:
            return  # No signals, do nothing

        symbol = event.symbol
        
        # Lọc ra các alerts chỉ thuộc về mã đang xét
        symbol_alerts = [a for a in alerts if a.symbol == symbol]
        if not symbol_alerts:
            return

        # 1. Khởi tạo bộ đếm điểm (Score)
        signal_score = 0
        has_volume_anomaly = False

        # 2. Phân tích và chấm điểm từng Alert
        for alert in symbol_alerts:
            logger.warning(
                ">>> ALERT [%s] %s | %s",
                alert.signal_type, alert.symbol, alert.message,
            )

            # --- Chấm điểm Moving Average ---
            if alert.signal_type == "MA_CROSSOVER":
                if "Golden cross" in alert.message:
                    signal_score += 1  # Tín hiệu Tăng (+1)
                elif "Death cross" in alert.message:
                    signal_score -= 1  # Tín hiệu Giảm (-1)

            # --- Chấm điểm Spike ---
            elif alert.signal_type == "SPIKE_DETECTED":
                if "spike UP" in alert.message:
                    signal_score += 1  # Bơm giá (+1)
                elif "spike DOWN" in alert.message:
                    signal_score -= 1  # Xả giá (-1)

            # --- Ghi nhận Volume ---
            elif alert.signal_type == "VOLUME_ANOMALY":
                has_volume_anomaly = True

        # 3. Áp dụng Volume Multiplier (Xác nhận từ dòng tiền)
        # Nếu có Volume đột biến, nhân đôi sức mạnh của tín hiệu hiện tại
        if has_volume_anomaly and signal_score != 0:
            logger.info("🔥 Volume Confirmation! Multiplying signal strength.")
            signal_score *= 2

        # 4. Quyết định (Decision Making) dựa trên Tổng điểm
        # Chỉ vào lệnh khi tín hiệu rõ ràng (điểm >= 1 hoặc <= -1)
        base_qty = self.base_quantities.get(symbol, 1.0)
        order_executed = False

        if signal_score >= 1:
            # Điểm dương -> Xu hướng Tăng -> MUA
            # Có thể thiết kế lượng mua tỉ lệ thuận với điểm số: qty = base_qty * signal_score
            actual_qty = base_qty * (1 if signal_score == 1 else 1.5) # Mua nhiều hơn chút nếu tín hiệu mạnh
            
            logger.info(
                "🟢 STRATEGY: STRONG BUY SIGNAL (Score: %d) -> BUY %.4f %s", 
                signal_score, actual_qty, symbol
            )
            self.order_manager.execute_order("BUY", symbol, actual_qty, event.price)
            order_executed = True

        elif signal_score <= -1:
            # Điểm âm -> Xu hướng Giảm -> BÁN
            actual_qty = base_qty * (1 if signal_score == -1 else 1.5)
            
            logger.info(
                "🔴 STRATEGY: STRONG SELL SIGNAL (Score: %d) -> SELL %.4f %s", 
                signal_score, actual_qty, symbol
            )
            self.order_manager.execute_order("SELL", symbol, actual_qty, event.price)
            order_executed = True

        else:
            # signal_score == 0: Tín hiệu nhiễu, đá nhau hoặc không đủ mạnh
            logger.info("⚪ STRATEGY: Conflicting or Weak Signals (Score: 0). HOLDING.")

        # 5. Cập nhật sổ sách nếu có giao dịch
        if order_executed:
            summary = self.order_manager.get_portfolio_summary()
            logger.info(
                "💰 PORTFOLIO: Balance=$%.2f | PnL=$%.2f | Positions=%s",
                summary['balance_usdt'],
                summary['realized_pnl'],
                summary['positions']
            )
            print("-" * 65)