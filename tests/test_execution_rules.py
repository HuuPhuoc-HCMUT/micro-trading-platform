from datetime import datetime, timezone

from trading_engine.market_rules import MarketRules
from trading_engine.order_manager import OrderManager


def test_order_manager_debits_fee_tax_and_slippage() -> None:
    manager = OrderManager(
        initial_balance=10000.0,
        persist_to_db=False,
        fee_rate_bps=10.0,
        sell_tax_bps=10.0,
        slippage_bps=10.0,
    )

    buy = manager.execute_order("BUY", "AAA", 10.0, 100.0, current_step=0)
    sell = manager.execute_order("SELL", "AAA", 10.0, 110.0, current_step=1)

    assert buy.status == "FILLED"
    assert sell.status == "FILLED"
    assert buy.price > 100.0
    assert sell.price < 110.0
    assert manager.total_fees > 0
    assert manager.total_taxes > 0
    assert manager.total_slippage > 0
    assert sell.trade_pnl is not None
    assert sell.trade_pnl < (110.0 - 100.0) * 10.0


def test_market_rules_round_lot_and_block_unsettled_sell() -> None:
    manager = OrderManager(
        initial_balance=10000.0,
        persist_to_db=False,
        market_rules=MarketRules(lot_size=100.0, settlement_bars=2),
    )

    buy = manager.execute_order("BUY", "AAA", 155.0, 10.0, current_step=0)
    early_sell = manager.execute_order("SELL", "AAA", 100.0, 11.0, current_step=1)
    settled_sell = manager.execute_order("SELL", "AAA", 100.0, 11.0, current_step=2)

    assert buy.status == "FILLED"
    assert buy.quantity == 100.0
    assert early_sell.status == "REJECTED"
    assert settled_sell.status == "FILLED"


def test_market_rules_can_enforce_trading_session() -> None:
    manager = OrderManager(
        initial_balance=10000.0,
        persist_to_db=False,
        market_rules=MarketRules(enforce_trading_session=True),
    )

    order = manager.execute_order(
        "BUY",
        "AAA",
        10.0,
        100.0,
        timestamp=datetime(2026, 1, 1, 8, 30, tzinfo=timezone.utc),
    )

    assert order.status == "REJECTED"
