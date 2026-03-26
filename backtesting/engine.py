import itertools
import logging
import math
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from statistics import fmean, pstdev

from cep_engine.moving_average import MovingAverageDetector
from cep_engine.probabilistic_signal import ProbabilisticSignalDetector
from cep_engine.spike_detector import SpikeDetector
from cep_engine.volume_anomaly import VolumeAnomalyDetector
from models.price_event import PriceEvent
from trading_engine.order_manager import OrderManager
from trading_engine.strategy import RuleBasedStrategy


@dataclass(slots=True)
class BacktestConfig:
    symbol: str = "BTC/USDT"
    initial_balance: float = 10000.0
    db_path: str = "trading_platform.db"
    start: str | None = None
    end: str | None = None
    ma_short_window: int = 15
    ma_long_window: int = 60
    spike_threshold_percent: float = 2.0
    spike_window_size: int = 5
    volume_window_size: int = 10
    volume_multiplier: float = 3.0
    volume_min_size: float = 0.5
    probability_window_size: int = 60
    probability_min_history: int = 20
    probability_learning_rate: float = 0.06
    probability_signal_threshold: float = 0.20
    max_balance_risk: float = 0.18
    min_trade_notional: float = 25.0
    verbose: bool = False


@contextmanager
def _muted_backtest_logs(enabled: bool):
    if enabled:
        yield
        return

    logger_names = [
        "trading_engine.strategy",
        "trading_engine.order_manager",
        "cep_engine.moving_average",
        "cep_engine.spike_detector",
        "cep_engine.volume_anomaly",
        "cep_engine.probabilistic_signal",
    ]
    original_levels = {}
    for name in logger_names:
        logger = logging.getLogger(name)
        original_levels[name] = logger.level
        logger.setLevel(logging.CRITICAL)

    try:
        yield
    finally:
        for name, level in original_levels.items():
            logging.getLogger(name).setLevel(level)


def _load_events_from_db(config: BacktestConfig) -> list[PriceEvent]:
    query = [
        "SELECT timestamp, open, high, low, close, volume FROM price_history",
        "WHERE 1=1",
    ]
    params: list[str] = []

    if config.start:
        query.append("AND timestamp >= ?")
        params.append(config.start)
    if config.end:
        query.append("AND timestamp <= ?")
        params.append(config.end)

    query.append("ORDER BY timestamp ASC")

    with sqlite3.connect(config.db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(" ".join(query), tuple(params)).fetchall()

    events: list[PriceEvent] = []
    for row in rows:
        events.append(
            PriceEvent(
                symbol=config.symbol,
                price=row["close"],
                volume=row["volume"],
                timestamp=datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00")),
                source="backtest",
                open=row["open"],
                high=row["high"],
                low=row["low"],
            )
        )
    return events


def _compute_max_drawdown(equity_curve: list[float]) -> float:
    if not equity_curve:
        return 0.0

    peak = equity_curve[0]
    max_drawdown = 0.0
    for equity in equity_curve:
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak
            max_drawdown = max(max_drawdown, drawdown)
    return max_drawdown


def _compute_sharpe(returns: list[float]) -> float:
    if len(returns) < 2:
        return 0.0
    avg_return = fmean(returns)
    volatility = pstdev(returns)
    if volatility <= 1e-12:
        return 0.0
    return (avg_return / volatility) * math.sqrt(len(returns))


def _extract_trade_stats(manager: OrderManager) -> tuple[int, float]:
    closed_trades = 0
    wins = 0
    for order in manager.order_history:
        if order.status != "FILLED" or order.side != "SELL":
            continue
        closed_trades += 1
        if order.trade_pnl is not None and float(order.trade_pnl) > 0:
            wins += 1
    win_rate = wins / closed_trades if closed_trades else 0.0
    return closed_trades, win_rate


def run_backtest(config: BacktestConfig) -> dict[str, float | int | str | list[float] | dict[str, float]]:
    events = _load_events_from_db(config)
    if not events:
        raise ValueError("No candles found in price_history for the selected backtest range.")

    manager = OrderManager(initial_balance=config.initial_balance, persist_to_db=False)
    strategy = RuleBasedStrategy(
        manager,
        max_balance_risk=config.max_balance_risk,
        min_trade_notional=config.min_trade_notional,
    )

    ma_detector = MovingAverageDetector(
        short_window=config.ma_short_window,
        long_window=config.ma_long_window,
    )
    spike_detector = SpikeDetector(
        threshold_percent=config.spike_threshold_percent,
        window_size=config.spike_window_size,
    )
    volume_detector = VolumeAnomalyDetector(
        window_size=config.volume_window_size,
        multiplier=config.volume_multiplier,
        min_volume=config.volume_min_size,
    )
    probabilistic_detector = ProbabilisticSignalDetector(
        window_size=config.probability_window_size,
        min_history=config.probability_min_history,
        learning_rate=config.probability_learning_rate,
        signal_threshold=config.probability_signal_threshold,
    )

    equity_curve: list[float] = []
    equity_returns: list[float] = []
    last_equity: float | None = None

    with _muted_backtest_logs(config.verbose):
        for event in events:
            alerts = []

            ma_alert = ma_detector.process(event)
            if ma_alert:
                alerts.append(ma_alert)

            spike_alert = spike_detector.process(event)
            if spike_alert:
                alerts.append(spike_alert)

            vol_alert = volume_detector.process(event)
            if vol_alert:
                alerts.append(vol_alert)

            probability_alert = probabilistic_detector.process(event)
            if probability_alert:
                alerts.append(probability_alert)

            strategy.execute(alerts, event)

            summary = manager.get_portfolio_summary({config.symbol: event.price})
            equity = float(summary["equity"])
            equity_curve.append(equity)
            if last_equity and last_equity > 0:
                equity_returns.append((equity - last_equity) / last_equity)
            last_equity = equity

    final_summary = manager.get_portfolio_summary({config.symbol: events[-1].price})
    closed_trades, win_rate = _extract_trade_stats(manager)
    total_return = (float(final_summary["equity"]) - config.initial_balance) / config.initial_balance
    max_drawdown = _compute_max_drawdown(equity_curve)
    sharpe_ratio = _compute_sharpe(equity_returns)

    objective_score = total_return - max_drawdown * 0.7 + sharpe_ratio * 0.1 + win_rate * 0.05

    return {
        "symbol": config.symbol,
        "candles": len(events),
        "orders": len(manager.order_history),
        "closed_trades": closed_trades,
        "win_rate": win_rate,
        "initial_balance": config.initial_balance,
        "ending_balance": float(final_summary["balance_usdt"]),
        "ending_equity": float(final_summary["equity"]),
        "realized_pnl": float(final_summary["realized_pnl"]),
        "unrealized_pnl": float(final_summary["total_unrealized_pnl"]),
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "sharpe_ratio": sharpe_ratio,
        "objective_score": objective_score,
        "params": {
            "ma_short_window": config.ma_short_window,
            "ma_long_window": config.ma_long_window,
            "spike_threshold_percent": config.spike_threshold_percent,
            "spike_window_size": config.spike_window_size,
            "volume_window_size": config.volume_window_size,
            "volume_multiplier": config.volume_multiplier,
            "volume_min_size": config.volume_min_size,
            "probability_window_size": config.probability_window_size,
            "probability_min_history": config.probability_min_history,
            "probability_learning_rate": config.probability_learning_rate,
            "probability_signal_threshold": config.probability_signal_threshold,
            "max_balance_risk": config.max_balance_risk,
            "min_trade_notional": config.min_trade_notional,
        },
        "equity_curve": equity_curve,
    }


def optimize_backtest(
    base_config: BacktestConfig,
    search_space: dict[str, list[int | float]],
) -> dict[str, object]:
    parameter_names = list(search_space.keys())
    parameter_values = [search_space[name] for name in parameter_names]

    if not parameter_names:
        result = run_backtest(base_config)
        return {"best_result": result, "results": [result]}

    results = []
    for candidate in itertools.product(*parameter_values):
        candidate_config = BacktestConfig(**asdict(base_config))
        for name, value in zip(parameter_names, candidate):
            setattr(candidate_config, name, value)
        results.append(run_backtest(candidate_config))

    best_result = max(results, key=lambda item: float(item["objective_score"]))
    return {
        "best_result": best_result,
        "results": results,
    }
