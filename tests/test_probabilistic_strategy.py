from datetime import datetime, timezone

from cep_engine.probabilistic_signal import ProbabilisticSignalDetector
from models.alert import Alert
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager
from trading_engine.strategy import RuleBasedStrategy


def build_event(price: float, volume: float = 10.0, symbol: str = "BTC/USDT") -> PriceEvent:
    return PriceEvent(
        symbol=symbol,
        price=price,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        source="test",
    )


def test_probabilistic_detector_emits_buy_signal_in_uptrend() -> None:
    detector = ProbabilisticSignalDetector(min_history=10, signal_threshold=0.12)
    alert = None

    for index in range(30):
        event = build_event(price=100 + index * 1.4, volume=12 + index * 0.3)
        candidate = detector.process(event)
        if candidate and candidate.direction == "BUY":
            alert = candidate

    assert alert is not None
    assert alert.signal_type == "PROBABILISTIC_ALPHA"
    assert alert.confidence > 0.5
    assert alert.metadata["up_probability"] > 0.5


def test_strategy_buys_then_reduces_when_edge_flips() -> None:
    manager = OrderManager(initial_balance=10000.0)
    strategy = RuleBasedStrategy(manager)

    buy_event = build_event(price=100.0)
    buy_alerts = [
        Alert(
            signal_type="PROBABILISTIC_ALPHA",
            symbol="BTC/USDT",
            severity="HIGH",
            message="BUY edge",
            triggered_at=datetime.now(timezone.utc),
            direction="BUY",
            confidence=0.82,
            score=0.74,
            expected_return=0.018,
            metadata={"realized_volatility": 0.006},
        ),
        Alert(
            signal_type="VOLUME_ANOMALY",
            symbol="BTC/USDT",
            severity="MEDIUM",
            message="Volume confirmation",
            triggered_at=datetime.now(timezone.utc),
            confidence=0.7,
            score=0.4,
            metadata={"volume_ratio": 3.5},
        ),
    ]

    strategy.execute(buy_alerts, buy_event)
    bought_qty = manager.get_position("BTC/USDT").quantity

    assert bought_qty > 0

    sell_event = build_event(price=98.0)
    sell_alerts = [
        Alert(
            signal_type="PROBABILISTIC_ALPHA",
            symbol="BTC/USDT",
            severity="HIGH",
            message="SELL edge",
            triggered_at=datetime.now(timezone.utc),
            direction="SELL",
            confidence=0.8,
            score=-0.9,
            expected_return=-0.022,
            metadata={"realized_volatility": 0.008},
        )
    ]

    strategy.execute(sell_alerts, sell_event)

    assert manager.get_position("BTC/USDT").quantity < bought_qty


def test_strategy_can_open_short_on_strong_negative_edge() -> None:
    manager = OrderManager(initial_balance=10000.0, persist_to_db=False)
    strategy = RuleBasedStrategy(manager, allow_short=True)

    event = build_event(price=100.0)
    alerts = [
        Alert(
            signal_type="PROBABILISTIC_ALPHA",
            symbol="BTC/USDT",
            severity="HIGH",
            message="SHORT edge",
            triggered_at=datetime.now(timezone.utc),
            direction="SELL",
            confidence=0.9,
            score=-1.1,
            expected_return=-0.03,
            metadata={"realized_volatility": 0.006, "downside_volatility": 0.008},
        )
    ]

    strategy.execute(alerts, event)

    assert manager.get_position("BTC/USDT").quantity < 0
