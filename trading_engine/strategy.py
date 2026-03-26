import logging
import math

from models.alert import Alert
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager

logger = logging.getLogger(__name__)

class RuleBasedStrategy:
    """
    Confluence strategy using probabilistic edge, expected value, and risk sizing.
    """

    def __init__(
        self,
        order_manager: OrderManager,
        max_balance_risk: float = 0.18,
        min_trade_notional: float = 25.0,
        allow_short: bool = True,
    ) -> None:
        self.order_manager = order_manager
        # Base trading quantities (Khối lượng vào lệnh cơ bản)
        self.base_quantities = {
            "BTC/USDT": 0.05,
            "ETH/USDT": 0.5,
            "SOL/USDT": 5.0
        }
        self.max_balance_risk = max_balance_risk
        self.min_trade_notional = min_trade_notional
        self.allow_short = allow_short

    def _aggregate_signal(self, alerts: list[Alert]) -> dict[str, float | bool]:
        weighted_edge = 0.0
        expected_return = 0.0
        confidence_sum = 0.0
        realized_volatility = 0.0
        downside_volatility = 0.0
        has_volume_confirmation = False

        for alert in alerts:
            logger.warning(
                ">>> ALERT [%s] %s | %s",
                alert.signal_type, alert.symbol, alert.message,
            )
            weight = max(alert.confidence, 0.35)

            if alert.signal_type == "VOLUME_ANOMALY":
                has_volume_confirmation = True
                confidence_sum += alert.confidence * 0.4
                continue

            direction_multiplier = 1.0
            if alert.direction == "SELL":
                direction_multiplier = -1.0

            score = alert.score if alert.score != 0 else direction_multiplier * max(alert.confidence - 0.5, 0.0) * 2.0
            weighted_edge += weight * score
            expected_return += weight * alert.expected_return
            confidence_sum += alert.confidence
            realized_volatility = max(
                realized_volatility,
                float(alert.metadata.get("realized_volatility", 0.0)),
                float(alert.metadata.get("volatility", 0.0)),
            )
            downside_volatility = max(
                downside_volatility,
                float(alert.metadata.get("downside_volatility", 0.0)),
            )

        if has_volume_confirmation:
            weighted_edge *= 1.15
            expected_return *= 1.10

        return {
            "edge": weighted_edge,
            "expected_return": expected_return,
            "confidence": min(0.99, confidence_sum / max(len(alerts), 1)),
            "realized_volatility": max(realized_volatility, 0.002),
            "downside_volatility": max(downside_volatility, 0.002),
            "has_volume_confirmation": has_volume_confirmation,
        }

    def _target_notional(self, symbol: str, price: float, signal: dict[str, float | bool]) -> float:
        edge = abs(float(signal["edge"]))
        confidence = float(signal["confidence"])
        expected_return = abs(float(signal["expected_return"]))
        realized_volatility = float(signal["realized_volatility"])
        downside_volatility = float(signal["downside_volatility"])
        current_equity = float(self.order_manager.get_portfolio_summary({symbol: price})["equity"])

        win_probability = min(0.99, max(0.01, 0.5 + min(edge, 1.5) / 3.0))
        reward_to_risk = max(expected_return / max(realized_volatility + downside_volatility, 0.003), 0.35)
        bayesian_edge = max((win_probability * reward_to_risk) - (1.0 - win_probability), 0.0)
        kelly_fraction = max(win_probability - ((1.0 - win_probability) / reward_to_risk), 0.0)
        capped_fraction = min((0.55 * kelly_fraction + 0.45 * bayesian_edge) * confidence, self.max_balance_risk)

        base_qty = self.base_quantities.get(symbol, 1.0)
        base_notional = base_qty * price
        adaptive_notional = max(base_notional * confidence * (1.0 + edge), self.min_trade_notional)
        capital_base = max(current_equity, self.order_manager.initial_balance * 0.25)
        return min(capital_base * capped_fraction + adaptive_notional * 0.25, capital_base * self.max_balance_risk)

    def analyze(self, alerts: list[Alert], symbol: str) -> dict[str, float | bool | str]:
        symbol_alerts = [a for a in alerts if a.symbol == symbol]
        if not symbol_alerts:
            return {
                "edge": 0.0,
                "expected_return": 0.0,
                "confidence": 0.0,
                "realized_volatility": 0.0,
                "downside_volatility": 0.0,
                "has_volume_confirmation": False,
                "action": "HOLD",
            }

        signal = self._aggregate_signal(symbol_alerts)
        position = self.order_manager.get_position(symbol)
        edge = float(signal["edge"])
        expected_return = float(signal["expected_return"])

        action = "HOLD"
        if abs(edge) >= 0.2 and abs(expected_return) >= 0.0005:
            if edge > 0:
                action = "BUY"
            elif position.quantity > 0:
                action = "SELL"
            elif self.allow_short:
                action = "SHORT"

        return {
            **signal,
            "action": action,
        }

    def execute(self, alerts: list[Alert], event: PriceEvent) -> None:
        """Evaluate CEP alerts with probability-aware position sizing."""
        if not alerts:
            return  # No signals, do nothing

        symbol = event.symbol
        
        # Lọc ra các alerts chỉ thuộc về mã đang xét
        symbol_alerts = [a for a in alerts if a.symbol == symbol]
        if not symbol_alerts:
            return

        signal = self.analyze(symbol_alerts, symbol)
        edge = float(signal["edge"])
        confidence = float(signal["confidence"])
        expected_return = float(signal["expected_return"])
        realized_volatility = float(signal["realized_volatility"])
        downside_volatility = float(signal["downside_volatility"])

        if abs(edge) < 0.2 or abs(expected_return) < 0.0005:
            logger.info(
                "⚪ STRATEGY: edge too small for %s | edge=%.3f expected=%.4f%% conf=%.2f",
                symbol,
                edge,
                expected_return * 100.0,
                confidence,
            )
            return

        target_notional = self._target_notional(symbol, event.price, signal)
        desired_signed_notional = target_notional if edge > 0 else -target_notional
        if not self.allow_short:
            desired_signed_notional = max(desired_signed_notional, 0.0)

        order_executed = False
        position = self.order_manager.get_position(symbol)
        current_signed_notional = position.quantity * event.price
        delta_notional = desired_signed_notional - current_signed_notional
        actual_qty = abs(delta_notional) / event.price if event.price > 0 else 0.0

        if delta_notional > 0:
            logger.info(
                "🟢 STRATEGY: BUY/COVER %s | edge=%.3f conf=%.2f exp=%.4f%% vol=%.4f%% dvol=%.4f%% qty=%.4f",
                symbol,
                edge,
                confidence,
                expected_return * 100.0,
                realized_volatility * 100.0,
                downside_volatility * 100.0,
                actual_qty,
            )
            if actual_qty * event.price >= self.min_trade_notional:
                self.order_manager.execute_order("BUY", symbol, actual_qty, event.price)
                order_executed = True

        elif delta_notional < 0:
            logger.info(
                "🔴 STRATEGY: SELL/SHORT %s | edge=%.3f conf=%.2f exp=%.4f%% vol=%.4f%% dvol=%.4f%% qty=%.4f",
                symbol,
                edge,
                confidence,
                expected_return * 100.0,
                realized_volatility * 100.0,
                downside_volatility * 100.0,
                actual_qty,
            )
            if actual_qty * event.price >= self.min_trade_notional:
                self.order_manager.execute_order("SELL", symbol, actual_qty, event.price)
                order_executed = True

        else:
            logger.info("⚪ STRATEGY: already near target exposure for %s.", symbol)

        if order_executed:
            current_prices = {event.symbol: event.price}
            summary = self.order_manager.get_portfolio_summary(current_prices)
            
            logger.info(
                "💰 PORTFOLIO: Balance=$%.2f | Equity=$%.2f | Realized=$%.2f | Unrealized=$%.2f",
                summary['balance_usdt'],
                summary['equity'],
                summary['realized_pnl'],
                summary['total_unrealized_pnl']
            )
            print("-" * 65)
