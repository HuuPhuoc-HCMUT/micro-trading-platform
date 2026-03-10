import logging
from datetime import datetime, timezone
from models.order import Order

logger = logging.getLogger(__name__)


class Position:
    """Stores the current position of a specific symbol."""
    def __init__(self):
        self.quantity: float = 0.0
        self.avg_entry: float = 0.0


class OrderManager:
    """Manages balances, positions, and executes paper trading orders."""

    def __init__(self, initial_balance: float = 10000.0) -> None:
        self.balance = initial_balance
        self.positions: dict[str, Position] = {}
        self.realized_pnl: float = 0.0
        self.order_history: list[Order] = []

    def execute_order(self, side: str, symbol: str, quantity: float, price: float) -> Order:
        """Executes a BUY/SELL order and updates balances."""
        
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
            if self.balance >= cost:
                self.balance -= cost
                
                total_cost_spent = (position.quantity * position.avg_entry) + cost
                position.quantity += quantity
                position.avg_entry = total_cost_spent / position.quantity
                
                order.status = "FILLED"
                logger.info(
                    "FILLED BUY: %.4f %s @ %.2f | New Balance: $%.2f",
                    quantity, symbol, price, self.balance
                )
            else:
                order.status = "REJECTED"
                logger.warning(
                    "REJECTED BUY: Insufficient balance. Need $%.2f, have $%.2f",
                    cost, self.balance
                )

        elif order.side == "SELL":
            if position.quantity >= quantity:
                revenue = quantity * price
                self.balance += revenue
                
                trade_pnl = (price - position.avg_entry) * quantity
                self.realized_pnl += trade_pnl
                
                position.quantity -= quantity
                if position.quantity == 0:
                    position.avg_entry = 0.0
                    
                order.status = "FILLED"
                logger.info(
                    "FILLED SELL: %.4f %s @ %.2f | Trade PnL: $%.2f | New Balance: $%.2f",
                    quantity, symbol, price, trade_pnl, self.balance
                )
            else:
                order.status = "REJECTED"
                logger.warning(
                    "REJECTED SELL: Insufficient position. Need %.4f, have %.4f",
                    quantity, position.quantity
                )

        self.order_history.append(order)
        return order

    def get_portfolio_summary(self) -> dict:
        """Returns an overview of the current portfolio."""
        return {
            "balance_usdt": self.balance,
            "realized_pnl": self.realized_pnl,
            "positions": {
                sym: {"quantity": pos.quantity, "avg_entry": pos.avg_entry}
                for sym, pos in self.positions.items() if pos.quantity > 0
            }
        }