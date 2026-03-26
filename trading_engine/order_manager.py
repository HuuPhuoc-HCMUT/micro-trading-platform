import logging
from datetime import datetime, timezone
from models.order import Order
from database.db import save_order, save_balance

logger = logging.getLogger(__name__)

class Position:
    """Stores the current position of a specific symbol."""
    def __init__(self):
        self.quantity: float = 0.0
        self.avg_entry: float = 0.0

class OrderManager:
    """Manages balances, positions, and executes paper trading orders."""

    def __init__(
        self,
        initial_balance: float = 10000.0,
        persist_to_db: bool = True,
        max_gross_exposure_multiple: float = 2.0,
    ) -> None:
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.persist_to_db = persist_to_db
        self.max_gross_exposure_multiple = max_gross_exposure_multiple
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.order_history: list[Order] = []

    def _estimate_gross_exposure(self, symbol: str, current_price: float) -> float:
        total = 0.0
        for current_symbol, position in self.positions.items():
            mark_price = current_price if current_symbol == symbol else position.avg_entry
            total += abs(position.quantity) * mark_price
        return total

    def _can_increase_exposure(self, symbol: str, incremental_notional: float, price: float) -> bool:
        gross_after = self._estimate_gross_exposure(symbol, price) + incremental_notional
        return gross_after <= self.initial_balance * self.max_gross_exposure_multiple

    def _increase_long(self, position: Position, quantity: float, price: float) -> None:
        total_cost_spent = (position.quantity * position.avg_entry) + (quantity * price)
        position.quantity += quantity
        position.avg_entry = total_cost_spent / position.quantity if position.quantity != 0 else 0.0

    def _increase_short(self, position: Position, quantity: float, price: float) -> None:
        current_short = abs(position.quantity)
        total_short_value = (current_short * position.avg_entry) + (quantity * price)
        new_short = current_short + quantity
        position.quantity = -new_short
        position.avg_entry = total_short_value / new_short if new_short != 0 else 0.0

    def execute_order(self, side: str, symbol: str, quantity: float, price: float) -> Order:
        """Executes a BUY/SELL order and updates balances."""
        quantity = max(quantity, 0.0)
        
        order = Order(
            side=side.upper(),
            symbol=symbol,
            quantity=quantity,
            price=price,
            status="PENDING",
            timestamp=datetime.now(timezone.utc)
        )

        if symbol not in self.positions:
            self.positions[symbol] = Position()
        
        position = self.positions[symbol]

        if order.side == "BUY":
            cost = quantity * price
            if position.quantity < 0:
                cover_qty = min(quantity, abs(position.quantity))
                cover_cost = cover_qty * price
                if self.balance < cover_cost:
                    order.status = "REJECTED"
                else:
                    trade_pnl = (position.avg_entry - price) * cover_qty
                    self.balance -= cover_cost
                    self.realized_pnl += trade_pnl
                    position.quantity += cover_qty
                    order.trade_pnl = trade_pnl
                    remaining_qty = quantity - cover_qty
                    if position.quantity == 0:
                        position.avg_entry = 0.0
                    if remaining_qty > 0:
                        remaining_cost = remaining_qty * price
                        if self.balance >= remaining_cost and self._can_increase_exposure(symbol, remaining_cost, price):
                            self.balance -= remaining_cost
                            self._increase_long(position, remaining_qty, price)
                        else:
                            remaining_qty = 0.0
                    order.quantity = cover_qty + remaining_qty
                    order.status = "FILLED" if order.quantity > 0 else "REJECTED"
                    logger.info(
                        "FILLED BUY/COVER: %.4f %s @ %.2f | Trade PnL: $%.2f | New Balance: $%.2f",
                        order.quantity, symbol, price, trade_pnl, self.balance
                    )
            elif self.balance >= cost and self._can_increase_exposure(symbol, cost, price):
                self.balance -= cost
                self._increase_long(position, quantity, price)
                order.status = "FILLED"
                logger.info(
                    "FILLED BUY: %.4f %s @ %.2f | New Balance: $%.2f",
                    quantity, symbol, price, self.balance
                )
            else:
                order.status = "REJECTED"
                logger.warning(
                    "REJECTED BUY: Insufficient buying power. Need $%.2f, have $%.2f",
                    cost, self.balance
                )

        elif order.side == "SELL":
            if position.quantity > 0:
                close_qty = min(quantity, position.quantity)
                revenue = close_qty * price
                trade_pnl = (price - position.avg_entry) * close_qty
                self.balance += revenue
                self.realized_pnl += trade_pnl
                position.quantity -= close_qty
                if position.quantity == 0:
                    position.avg_entry = 0.0

                short_qty = quantity - close_qty
                if short_qty > 0 and self._can_increase_exposure(symbol, short_qty * price, price):
                    self.balance += short_qty * price
                    self._increase_short(position, short_qty, price)
                else:
                    short_qty = 0.0

                order.quantity = close_qty + short_qty
                order.status = "FILLED" if order.quantity > 0 else "REJECTED"
                order.trade_pnl = trade_pnl
                logger.info(
                    "FILLED SELL/SHORT: %.4f %s @ %.2f | Trade PnL: $%.2f | New Balance: $%.2f",
                    order.quantity, symbol, price, trade_pnl, self.balance
                )
            elif self._can_increase_exposure(symbol, quantity * price, price):
                revenue = quantity * price
                self.balance += revenue
                self._increase_short(position, quantity, price)
                order.status = "FILLED"
                logger.info(
                    "FILLED SHORT SELL: %.4f %s @ %.2f | New Balance: $%.2f",
                    quantity, symbol, price, self.balance
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
            "positions": positions_summary
        }

    def get_position(self, symbol: str) -> Position:
        if symbol not in self.positions:
            self.positions[symbol] = Position()
        return self.positions[symbol]
