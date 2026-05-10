import logging
import math
from collections import deque
from datetime import datetime, timezone
from statistics import fmean, pstdev

from models.alert import Alert
from models.price_event import PriceEvent

logger = logging.getLogger(__name__)


class SymbolState:
    def __init__(self, maxlen: int) -> None:
        self.prices: deque[float] = deque(maxlen=maxlen)
        self.volumes: deque[float] = deque(maxlen=maxlen)
        self.returns: deque[float] = deque(maxlen=maxlen)
        self.weights: list[float] = [0.0] * 10
        self.prev_features: list[float] | None = None
        self.prev_price: float | None = None


class ProbabilisticSignalDetector:
    """Online-learning detector that mixes statistics and probability forecasting."""

    def __init__(
        self,
        window_size: int = 60,
        min_history: int = 20,
        learning_rate: float = 0.08,
        l2_penalty: float = 0.002,
        signal_threshold: float = 0.18,
    ) -> None:
        self.window_size = window_size
        self.min_history = min_history
        self.learning_rate = learning_rate
        self.l2_penalty = l2_penalty
        self.signal_threshold = signal_threshold
        self.state: dict[str, SymbolState] = {}

    def _sigmoid(self, value: float) -> float:
        clipped = max(min(value, 30.0), -30.0)
        return 1.0 / (1.0 + math.exp(-clipped))

    def _bounded(self, value: float, scale: float = 1.0) -> float:
        return math.tanh(value / max(scale, 1e-9))

    def _logit(self, probability: float) -> float:
        clipped = min(max(probability, 1e-6), 1.0 - 1e-6)
        return math.log(clipped / (1.0 - clipped))

    def _ensure_state(self, symbol: str) -> SymbolState:
        if symbol not in self.state:
            self.state[symbol] = SymbolState(self.window_size)
        return self.state[symbol]

    def _update_online_model(self, state: SymbolState, current_price: float) -> None:
        if state.prev_features is None or state.prev_price is None or state.prev_price <= 0:
            state.prev_price = current_price
            return

        realized_return = (current_price - state.prev_price) / state.prev_price
        label = 1.0 if realized_return > 0 else 0.0

        logit = sum(weight * feature for weight, feature in zip(state.weights, state.prev_features))
        prediction = self._sigmoid(logit)
        error = label - prediction

        for index, feature in enumerate(state.prev_features):
            state.weights[index] += self.learning_rate * (
                error * feature - self.l2_penalty * state.weights[index]
            )

        state.prev_price = current_price

    def _feature_value(self, values: list[float], lookback: int) -> float:
        if len(values) <= lookback:
            return 0.0
        base = values[-lookback - 1]
        if base == 0:
            return 0.0
        return (values[-1] - base) / base

    def _build_features(self, state: SymbolState, event: PriceEvent) -> tuple[list[float], dict[str, float]]:
        prices = list(state.prices)
        volumes = list(state.volumes)
        returns = list(state.returns)

        short_momentum = self._feature_value(prices, 3)
        medium_momentum = self._feature_value(prices, 8)

        mean_price = fmean(prices)
        price_std = pstdev(prices) if len(prices) > 1 else 0.0
        price_zscore = (event.price - mean_price) / price_std if price_std > 1e-9 else 0.0

        mean_volume = fmean(volumes)
        volume_std = pstdev(volumes) if len(volumes) > 1 else 0.0
        volume_zscore = (event.volume - mean_volume) / volume_std if volume_std > 1e-9 else 0.0

        realized_volatility = pstdev(returns) if len(returns) > 1 else 0.0
        trend_consistency = fmean(returns[-5:]) if returns else 0.0
        downside_returns = [value for value in returns if value < 0]
        downside_volatility = pstdev(downside_returns) if len(downside_returns) > 1 else 0.0
        return_mean = fmean(returns) if returns else 0.0
        return_std = pstdev(returns) if len(returns) > 1 else 0.0
        return_zscore = ((returns[-1] - return_mean) / return_std) if len(returns) > 1 and return_std > 1e-9 else 0.0

        recent_prices = prices[-10:] if len(prices) >= 10 else prices
        range_low = min(recent_prices)
        range_high = max(recent_prices)
        range_position = 0.0
        if range_high > range_low:
            range_position = ((event.price - range_low) / (range_high - range_low)) - 0.5

        mean_reversion_pressure = -price_zscore * max(realized_volatility, 0.002)
        risk_adjusted_trend = trend_consistency / max(realized_volatility, 0.002)

        features = [
            1.0,
            self._bounded(short_momentum, 0.01),
            self._bounded(medium_momentum, 0.02),
            self._bounded(price_zscore, 2.0),
            self._bounded(volume_zscore, 2.0),
            self._bounded(trend_consistency, max(realized_volatility, 0.001)),
            self._bounded(range_position, 0.35),
            self._bounded(downside_volatility, max(realized_volatility, 0.001)),
            self._bounded(mean_reversion_pressure, max(realized_volatility, 0.002)),
            self._bounded(return_zscore, 2.0),
        ]

        diagnostics = {
            "short_momentum": short_momentum,
            "medium_momentum": medium_momentum,
            "price_zscore": price_zscore,
            "volume_zscore": volume_zscore,
            "realized_volatility": realized_volatility,
            "downside_volatility": downside_volatility,
            "trend_consistency": trend_consistency,
            "range_position": range_position,
            "mean_reversion_pressure": mean_reversion_pressure,
            "risk_adjusted_trend": risk_adjusted_trend,
            "return_zscore": return_zscore,
        }
        return features, diagnostics

    def process(self, event: PriceEvent) -> Alert | None:
        state = self._ensure_state(event.symbol)
        self._update_online_model(state, event.price)

        if state.prices:
            last_price = state.prices[-1]
            if last_price > 0:
                state.returns.append((event.price - last_price) / last_price)

        state.prices.append(event.price)
        state.volumes.append(event.volume)

        if len(state.prices) < self.min_history:
            state.prev_price = event.price
            return None

        features, diagnostics = self._build_features(state, event)
        ml_logit = sum(weight * feature for weight, feature in zip(state.weights, features))
        ml_probability = self._sigmoid(ml_logit)

        stat_score = (
            0.90 * diagnostics["risk_adjusted_trend"]
            + 0.55 * (diagnostics["short_momentum"] / max(diagnostics["realized_volatility"], 0.002))
            + 0.35 * (diagnostics["medium_momentum"] / max(diagnostics["realized_volatility"], 0.002))
            + 0.30 * diagnostics["volume_zscore"]
            - 0.55 * diagnostics["price_zscore"]
            - 0.40 * (diagnostics["downside_volatility"] / max(diagnostics["realized_volatility"], 0.002))
            + 0.20 * diagnostics["range_position"]
            - 0.15 * diagnostics["return_zscore"]
        )
        stat_probability = self._sigmoid(stat_score)

        sample_confidence = min(len(state.returns) / max(self.window_size / 2, 1), 1.0)
        ensemble_logit = (
            0.50 * self._logit(ml_probability)
            + 0.35 * self._logit(stat_probability)
            + 0.15 * self._logit(0.5 + diagnostics["range_position"] * 0.5)
        )
        up_probability = self._sigmoid((0.25 + 0.75 * sample_confidence) * ensemble_logit)
        down_probability = 1.0 - up_probability

        expected_return = (
            0.35 * diagnostics["short_momentum"]
            + 0.25 * diagnostics["medium_momentum"]
            + 0.18 * diagnostics["trend_consistency"]
            + 0.10 * diagnostics["mean_reversion_pressure"]
            + 0.08 * diagnostics["volume_zscore"] * diagnostics["short_momentum"]
            + 0.07 * ((up_probability - 0.5) * max(diagnostics["realized_volatility"], 0.002))
            - 0.18 * diagnostics["downside_volatility"]
            - 0.06 * diagnostics["return_zscore"] * max(diagnostics["realized_volatility"], 0.002)
        )

        volatility_floor = max(diagnostics["realized_volatility"], 0.002)
        normalized_edge = (
            0.50 * (up_probability - down_probability)
            + 0.25 * self._bounded(expected_return, volatility_floor)
            + 0.15 * self._bounded(diagnostics["risk_adjusted_trend"], 1.5)
            - 0.10 * self._bounded(diagnostics["downside_volatility"], volatility_floor)
        )

        state.prev_features = features
        state.prev_price = event.price

        if abs(normalized_edge) < self.signal_threshold:
            return None

        direction = "BUY" if normalized_edge > 0 else "SELL"
        confidence = min(0.99, 0.5 + abs(normalized_edge) / 2.0)
        severity = "HIGH" if confidence >= 0.75 else "MEDIUM"

        return Alert(
            signal_type="PROBABILISTIC_ALPHA",
            symbol=event.symbol,
            severity=severity,
            message=(
                f"{direction} edge detected: p_up={up_probability:.2%}, "
                f"expected_return={expected_return:.4%}, vol={diagnostics['realized_volatility']:.4%}, "
                f"downside_vol={diagnostics['downside_volatility']:.4%}"
            ),
            triggered_at=datetime.now(timezone.utc),
            direction=direction,
            confidence=confidence,
            score=normalized_edge,
            expected_return=expected_return,
            metadata={
                "up_probability": round(up_probability, 6),
                "down_probability": round(down_probability, 6),
                "ml_probability": round(ml_probability, 6),
                "stat_probability": round(stat_probability, 6),
                "short_momentum": round(diagnostics["short_momentum"], 6),
                "medium_momentum": round(diagnostics["medium_momentum"], 6),
                "price_zscore": round(diagnostics["price_zscore"], 6),
                "volume_zscore": round(diagnostics["volume_zscore"], 6),
                "realized_volatility": round(diagnostics["realized_volatility"], 6),
                "downside_volatility": round(diagnostics["downside_volatility"], 6),
                "trend_consistency": round(diagnostics["trend_consistency"], 6),
                "range_position": round(diagnostics["range_position"], 6),
                "risk_adjusted_trend": round(diagnostics["risk_adjusted_trend"], 6),
                "return_zscore": round(diagnostics["return_zscore"], 6),
            },
        )
