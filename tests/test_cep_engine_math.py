from datetime import datetime, timezone

from cep_engine.probabilistic_signal import ProbabilisticSignalDetector
from cep_engine.spike_detector import SpikeDetector
from cep_engine.volume_anomaly import VolumeAnomalyDetector
from models.price_event import PriceEvent


def build_event(price: float, volume: float = 10.0, symbol: str = "TEST") -> PriceEvent:
    return PriceEvent(
        symbol=symbol,
        price=price,
        volume=volume,
        timestamp=datetime.now(timezone.utc),
        source="test",
    )


def test_volume_anomaly_requires_price_confirmation_after_cost() -> None:
    detector = VolumeAnomalyDetector(
        window_size=3,
        multiplier=2.0,
        min_volume=1.0,
        min_price_move=0.001,
        transaction_cost_bps=12.0,
    )

    for price in [100.0, 100.02, 100.01]:
        assert detector.process(build_event(price=price, volume=1.0)) is None

    no_edge = detector.process(build_event(price=100.05, volume=4.0))
    assert no_edge is None

    buy_alert = detector.process(build_event(price=100.40, volume=4.0))
    assert buy_alert is not None
    assert buy_alert.direction == "BUY"
    assert buy_alert.expected_return > 0
    assert buy_alert.score > 0


def test_volume_anomaly_can_emit_sell_when_distribution_volume_hits() -> None:
    detector = VolumeAnomalyDetector(
        window_size=3,
        multiplier=2.0,
        min_volume=1.0,
        min_price_move=0.001,
        transaction_cost_bps=12.0,
    )

    for price in [100.0, 100.02, 100.01]:
        detector.process(build_event(price=price, volume=1.0))

    sell_alert = detector.process(build_event(price=99.60, volume=4.0))
    assert sell_alert is not None
    assert sell_alert.direction == "SELL"
    assert sell_alert.expected_return < 0
    assert sell_alert.score < 0


def test_spike_detector_ignores_move_that_cannot_clear_cost() -> None:
    detector = SpikeDetector(
        threshold_percent=0.2,
        window_size=3,
        transaction_cost_bps=12.0,
    )

    assert detector.process(build_event(price=100.00)) is None
    assert detector.process(build_event(price=100.05)) is None
    alert = detector.process(build_event(price=100.21))

    assert alert is None


def test_spike_detector_emits_net_positive_continuation_signal() -> None:
    detector = SpikeDetector(
        threshold_percent=1.0,
        window_size=3,
        transaction_cost_bps=12.0,
    )

    for price in [100.0, 100.2, 101.6]:
        alert = detector.process(build_event(price=price))

    assert alert is not None
    assert alert.direction == "BUY"
    assert alert.expected_return > 0
    assert 0 < alert.metadata["continuation_quality"] <= 1


def test_probabilistic_detector_filters_tiny_net_expected_return() -> None:
    detector = ProbabilisticSignalDetector(
        min_history=8,
        signal_threshold=0.05,
        transaction_cost_bps=12.0,
        min_net_expected_return=0.0004,
    )

    alerts = []
    for index in range(30):
        event = build_event(price=100 + index * 0.005, volume=10.0)
        candidate = detector.process(event)
        if candidate is not None:
            alerts.append(candidate)

    assert alerts == []
