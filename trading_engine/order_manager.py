import logging
from datetime import datetime, timezone
from models.order import Order
from database.db import save_order, save_balance
from trading_engine.market_rules import MarketRules

logger = logging.getLogger(__name__)

class Position:
    """Stores the current position of a specific symbol."""
    def __init__(self):
        self.quantity: float = 0.0
        self.avg_entry: float = 0.0
        self.long_lots: list[dict[str, float | int]] = []

    def settled_long_quantity(self, current_step: int | None, settlement_bars: int) -> float:
        if self.quantity <= 0:
            return 0.0
        if current_step is None or settlement_bars <= 0:
            return self.quantity
        settled = 0.0
        for lot in self.long_lots:
            if current_step - int(lot["step"]) >= settlement_bars:
                settled += float(lot["quantity"])
        return min(settled, self.quantity)

class OrderManager:
    """Manages balances, positions, and executes paper trading orders."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        persist_to_db: bool = True,
        max_gross_exposure_multiple: float = 2.0,
        fee_rate_bps: float = 8.0,
        sell_tax_bps: float = 10.0,
        slippage_bps: float = 5.0,
        market_rules: MarketRules | None = None,
    ) -> None:
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.persist_to_db = persist_to_db
        self.max_gross_exposure_multiple = max_gross_exposure_multiple
        self.fee_rate = fee_rate_bps / 10_000.0
        self.sell_tax_rate = sell_tax_bps / 10_000.0
        self.slippage_rate = slippage_bps / 10_000.0
        self.market_rules = market_rules or MarketRules()
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.order_history: list[Order] = []
        self.total_fees: float = 0.0
        self.total_taxes: float = 0.0
        self.total_slippage: float = 0.0

    def _estimate_gross_exposure(self, symbol: str, current_price: float) -> float:
        total = 0.0
        for current_symbol, position in self.positions.items():
            mark_price = current_price if current_symbol == symbol else position.avg_entry
            total += abs(position.quantity) * mark_price
        return total

    def _can_increase_exposure(self, symbol: str, incremental_notional: float, price: float) -> bool:
        gross_after = self._estimate_gross_exposure(symbol, price) + incremental_notional
        return gross_after <= self.initial_balance * self.max_gross_exposure_multiple

    def _increase_long(self, position: Position, quantity: float, effective_price: float, current_step: int | None) -> None:
        total_cost_spent = (position.quantity * position.avg_entry) + (quantity * effective_price)
        position.quantity += quantity
        position.avg_entry = total_cost_spent / position.quantity if position.quantity != 0 else 0.0
        position.long_lots.append({"quantity": quantity, "step": current_step or 0})

    def _increase_short(self, position: Position, quantity: float, price: float) -> None:
        current_short = abs(position.quantity)
        total_short_value = (current_short * position.avg_entry) + (quantity * price)
        new_short = current_short + quantity
        position.quantity = -new_short
        position.avg_entry = total_short_value / new_short if new_short != 0 else 0.0

    def _consume_long_lots(self, position: Position, quantity: float, current_step: int | None) -> None:
        remaining = quantity
        updated_lots: list[dict[str, float | int]] = []
        for lot in position.long_lots:
            lot_qty = float(lot["quantity"])
            if remaining <= 0:
                updated_lots.append(lot)
                continue
            consumed = min(lot_qty, remaining)
            remaining -= consumed
            leftover = lot_qty - consumed
            if leftover > 1e-12:
                updated_lots.append({"quantity": leftover, "step": int(lot["step"])})
        position.long_lots = updated_lots

    def _execution_price(self, side: str, price: float) -> float:
        normalized = self.market_rules.normalize_price(price)
        if side.upper() == "BUY":
            return normalized * (1.0 + self.slippage_rate)
        return normalized * (1.0 - self.slippage_rate)

    def _costs(self, side: str, quantity: float, execution_price: float, reference_price: float) -> tuple[float, float, float]:
        notional = quantity * execution_price
        fee = notional * self.fee_rate
        tax = notional * self.sell_tax_rate if side.upper() == "SELL" else 0.0
        slippage = abs(execution_price - reference_price) * quantity
        return fee, tax, slippage

    def execute_order(
        self,
        side: str,
        symbol: str,
        quantity: float,
        price: float,
        *,
        timestamp: datetime | None = None,
        reason: str | None = None,
        current_step: int | None = None,
        reference_price: float | None = None,
    ) -> Order:
        """Executes a BUY/SELL order and updates balances."""
        side = side.upper()
        requested_price = price
        price = self.market_rules.normalize_price(price)
        quantity = self.market_rules.normalize_quantity(quantity)
        
        order = Order(
            side=side,
            symbol=symbol,
            quantity=quantity,
            price=price,
            status="PENDING",
            timestamp=timestamp or datetime.now(timezone.utc),
            reason=reason,
        )

        if symbol not in self.positions:
            self.positions[symbol] = Position()
        
        position = self.positions[symbol]

        if quantity <= 0:
            order.status = "REJECTED"
        elif not self.market_rules.is_session_open(order.timestamp):
            order.status = "REJECTED"
            logger.warning("REJECTED %s: market session closed for %s", side, symbol)
        elif not self.market_rules.is_price_inside_band(price, reference_price):
            order.status = "REJECTED"
            logger.warning("REJECTED %s: price %.2f outside band for %s", side, price, symbol)

        if order.status == "REJECTED":
            self.order_history.append(order)
            return order

        execution_price = self._execution_price(side, price)
        fee, tax, slippage = self._costs(side, quantity, execution_price, requested_price)
        order.price = execution_price
        order.fee = fee
        order.tax = tax
        order.slippage = slippage

        if order.side == "BUY":
            cost = quantity * execution_price
            if position.quantity < 0:
                cover_qty = min(quantity, abs(position.quantity))
                cover_cost = cover_qty * execution_price
                cover_fee, _, cover_slippage = self._costs(order.side, cover_qty, execution_price, requested_price)
                if self.balance < cover_cost + cover_fee:
                    order.status = "REJECTED"
                else:
                    trade_pnl = (position.avg_entry - execution_price) * cover_qty - cover_fee
                    self.balance -= cover_cost + cover_fee
                    self.realized_pnl += trade_pnl
                    self.total_fees += cover_fee
                    self.total_slippage += cover_slippage
                    position.quantity += cover_qty
                    order.trade_pnl = trade_pnl
                    remaining_qty = quantity - cover_qty
                    if position.quantity == 0:
                        position.avg_entry = 0.0
                    if remaining_qty > 0:
                        remaining_cost = remaining_qty * execution_price
                        remaining_fee, _, remaining_slippage = self._costs(order.side, remaining_qty, execution_price, requested_price)
                        if self.balance >= remaining_cost + remaining_fee and self._can_increase_exposure(symbol, remaining_cost, execution_price):
                            self.balance -= remaining_cost + remaining_fee
                            self.total_fees += remaining_fee
                            self.total_slippage += remaining_slippage
                            effective_entry = (remaining_cost + remaining_fee) / remaining_qty
                            self._increase_long(position, remaining_qty, effective_entry, current_step)
                        else:
                            remaining_qty = 0.0
                    order.quantity = cover_qty + remaining_qty
                    order.status = "FILLED" if order.quantity > 0 else "REJECTED"
                    logger.info(
                        "FILLED BUY/COVER: %.4f %s @ %.2f | Trade PnL: $%.2f | New Balance: $%.2f",
                        order.quantity, symbol, execution_price, trade_pnl, self.balance
                    )
            elif self.balance >= cost + fee and self._can_increase_exposure(symbol, cost, execution_price):
                self.balance -= cost + fee
                self.total_fees += fee
                self.total_slippage += slippage
                effective_entry = (cost + fee) / quantity
                self._increase_long(position, quantity, effective_entry, current_step)
                order.status = "FILLED"
                logger.info(
                    "FILLED BUY: %.4f %s @ %.2f | Fee: $%.2f | New Balance: $%.2f",
                    quantity, symbol, execution_price, fee, self.balance
                )
            else:
                order.status = "REJECTED"
                logger.warning(
                    "REJECTED BUY: Insufficient buying power. Need $%.2f, have $%.2f",
                    cost, self.balance
                )

        elif order.side == "SELL":
            if position.quantity > 0:
                available_qty = position.settled_long_quantity(current_step, self.market_rules.settlement_bars)
                close_qty = min(quantity, available_qty)
                if close_qty <= 0:
                    order.status = "REJECTED"
                    logger.warning("REJECTED SELL: no settled shares available for %s", symbol)
                    self.order_history.append(order)
                    return order
                revenue = close_qty * execution_price
                close_fee, close_tax, close_slippage = self._costs(order.side, close_qty, execution_price, requested_price)
                trade_pnl = (execution_price - position.avg_entry) * close_qty - close_fee - close_tax
                self.balance += revenue - close_fee - close_tax
                self.realized_pnl += trade_pnl
                self.total_fees += close_fee
                self.total_taxes += close_tax
                self.total_slippage += close_slippage
                position.quantity -= close_qty
                self._consume_long_lots(position, close_qty, current_step)
                if position.quantity == 0:
                    position.avg_entry = 0.0
                    position.long_lots = []

                short_qty = quantity - close_qty
                if short_qty > 0 and self._can_increase_exposure(symbol, short_qty * execution_price, execution_price):
                    short_revenue = short_qty * execution_price
                    short_fee, short_tax, short_slippage = self._costs(order.side, short_qty, execution_price, requested_price)
                    self.balance += short_revenue - short_fee - short_tax
                    self.total_fees += short_fee
                    self.total_taxes += short_tax
                    self.total_slippage += short_slippage
                    self._increase_short(position, short_qty, execution_price)
                else:
                    short_qty = 0.0

                order.quantity = close_qty + short_qty
                order.status = "FILLED" if order.quantity > 0 else "REJECTED"
                order.trade_pnl = trade_pnl
                logger.info(
                    "FILLED SELL/SHORT: %.4f %s @ %.2f | Trade PnL: $%.2f | Fee/Tax: $%.2f | New Balance: $%.2f",
                    order.quantity, symbol, execution_price, trade_pnl, close_fee + close_tax, self.balance
                )
            elif self._can_increase_exposure(symbol, quantity * execution_price, execution_price):
                revenue = quantity * execution_price
                self.balance += revenue - fee - tax
                self.total_fees += fee
                self.total_taxes += tax
                self.total_slippage += slippage
                self._increase_short(position, quantity, execution_price)
                order.status = "FILLED"
                logger.info(
                    "FILLED SHORT SELL: %.4f %s @ %.2f | Fee/Tax: $%.2f | New Balance: $%.2f",
                    quantity, symbol, execution_price, fee + tax, self.balance
                )
            else:
                order.status = "REJECTED"
                logger.warning(
                    "REJECTED SELL: Exposure cap reached for %.4f %s",
                    quantity, symbol
                )

        # 1. Lưu lệnh vừa đặt vào DB (dù khớp hay bị từ chối)
        if self.persist_to_db:
            save_order(order)
        
        # 2. Nếu lệnh khớp thành công làm thay đổi số dư, lưu cập nhật số dư mới
        if self.persist_to_db and order.status == "FILLED":
            save_balance(self.balance, self.realized_pnl)
        # -----------------------------
        
        self.order_history.append(order)
        return order

    def get_portfolio_summary(self, current_prices: dict[str, float] = None) -> dict:
        """
        Returns an overview of the portfolio, including Unrealized P&L.
        Args:
            current_prices: A dict mapping symbol to its current market price.
        """
        if current_prices is None:
            current_prices = {}

        total_unrealized_pnl = 0.0
        positions_summary = {}

        for sym, pos in self.positions.items():
            if pos.quantity != 0:
                current_price = current_prices.get(sym, pos.avg_entry)
                unrealized = (current_price - pos.avg_entry) * pos.quantity
                market_value = pos.quantity * current_price
                total_unrealized_pnl += unrealized

                positions_summary[sym] = {
                    "quantity": pos.quantity,
                    "avg_entry": pos.avg_entry,
                    "current_price": current_price,
                    "unrealized_pnl": unrealized,
                    "market_value": market_value,
                    "side": "LONG" if pos.quantity > 0 else "SHORT",
                }

        equity = self.balance + sum(p["market_value"] for p in positions_summary.values())

        return {
            "balance_usdt": self.balance,
            "equity": equity,
            "realized_pnl": self.realized_pnl,
            "total_unrealized_pnl": total_unrealized_pnl,
            "total_fees": self.total_fees,
            "total_taxes": self.total_taxes,
            "total_slippage": self.total_slippage,
            "positions": positions_summary
        }

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position()
        return self.positions[symbol]
