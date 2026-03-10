import logging

from models.alert import Alert
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager

logger = logging.getLogger(__name__)


class RuleBasedStrategy:
    """Evaluates alerts and executes trading orders based on predefined rules."""

    def __init__(self, order_manager: OrderManager) -> None:
        self.order_manager = order_manager
        # Default trading quantities for each symbol
        self.trade_quantities = {
            "BTC/USDT": 0.05,
            "ETH/USDT": 0.5,
            "SOL/USDT": 5.0
        }

    def execute(self, alerts: list[Alert], event: PriceEvent) -> None:
        """Processes a list of alerts and executes orders if conditions are met."""
        order_executed = False

        for alert in alerts:
            logger.warning(
                ">>> ALERT [%s] %s | severity=%s | %s",
                alert.signal_type, alert.symbol, alert.severity, alert.message,
            )

            qty = self.trade_quantities.get(event.symbol, 1.0)

            # Strategy 1: Moving Average Crossover
            if alert.signal_type == "MA_CROSSOVER":
                if "Golden cross" in alert.message:
                    logger.info("STRATEGY: MA Uptrend -> BUY %.4f %s", qty, event.symbol)
                    self.order_manager.execute_order("BUY", event.symbol, qty, event.price)
                    order_executed = True
                elif "Death cross" in alert.message:
                    logger.info("STRATEGY: MA Downtrend -> SELL %.4f %s", qty, event.symbol)
                    self.order_manager.execute_order("SELL", event.symbol, qty, event.price)
                    order_executed = True

            # Strategy 2: Spike Detection
            elif alert.signal_type == "SPIKE_DETECTED":
                if "spike UP" in alert.message:
                    logger.info("STRATEGY: Sudden Pump -> BUY %.4f %s", qty, event.symbol)
                    self.order_manager.execute_order("BUY", event.symbol, qty, event.price)
                    order_executed = True
                elif "spike DOWN" in alert.message:
                    logger.info("STRATEGY: Sudden Dump -> SELL %.4f %s", qty, event.symbol)
                    self.order_manager.execute_order("SELL", event.symbol, qty, event.price)
                    order_executed = True

            # Strategy 3: Volume Anomaly
            elif alert.signal_type == "VOLUME_ANOMALY":
                logger.info("STRATEGY: High volume detected on %s. Watching closely...", event.symbol)

        # Print Portfolio state only if an order was actually attempted
        if order_executed:
            summary = self.order_manager.get_portfolio_summary()
            logger.info(
                "PORTFOLIO: Balance=$%.2f | PnL=$%.2f | Positions=%s",
                summary['balance_usdt'],
                summary['realized_pnl'],
                summary['positions']
            )
            print("-" * 60)