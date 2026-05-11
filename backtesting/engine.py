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
from trading_engine.market_rules import MarketRules
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
    allow_short: bool = False
    fee_rate_bps: float = 8.0
    sell_tax_bps: float = 10.0
    slippage_bps: float = 5.0
    lot_size: float = 0.0
    price_tick: float = 0.0
    price_band_percent: float = 0.0
    settlement_bars: int = 0
    enforce_trading_session: bool = False
    enable_regime_filter: bool = False
    regime_ma_window: int = 30
    stop_loss_pct: float = 0.0
    take_profit_pct: float = 0.0
    trailing_stop_pct: float = 0.0
    time_stop_bars: int = 0
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


def _load_raw_rows(db_path: str, start: str | None = None, end: str | None = None) -> list[sqlite3.Row]:
    query = [
        "SELECT timestamp, open, high, low, close, volume FROM price_history",
        "WHERE 1=1",
    ]
    params: list[str] = []
    if start:
        query.append("AND timestamp >= ?")
        params.append(start)
    if end:
        query.append("AND timestamp <= ?")
        params.append(end)
    query.append("ORDER BY timestamp ASC")
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(" ".join(query), tuple(params)).fetchall()


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


def _profit_factor(trade_reports: list[dict[str, float | int | str]]) -> float:
    gross_profit = sum(float(trade["pnl"]) for trade in trade_reports if float(trade["pnl"]) > 0)
    gross_loss = abs(sum(float(trade["pnl"]) for trade in trade_reports if float(trade["pnl"]) < 0))
    if gross_loss <= 1e-12:
        return gross_profit if gross_profit > 0 else 0.0
    return gross_profit / gross_loss


def _regime_allows_buy(price_history: list[float], window: int) -> bool:
    if window <= 1 or len(price_history) < window + 1:
        return True
    current_ma = fmean(price_history[-window:])
    previous_ma = fmean(price_history[-window - 1:-1])
    current_price = price_history[-1]
    return current_price >= current_ma and current_ma >= previous_ma


def _new_open_trade(event: PriceEvent, quantity: float, entry_price: float, step: int, reason: str | None) -> dict[str, float | int | str]:
    return {
        "entry_time": event.timestamp.isoformat(),
        "entry_step": step,
        "entry_price": entry_price,
        "quantity": quantity,
        "highest_price": event.high or event.price,
        "lowest_price": event.low or event.price,
        "entry_reason": reason or "ENTRY",
    }


def _update_open_trade(open_trade: dict[str, float | int | str], event: PriceEvent) -> None:
    high = event.high or event.price
    low = event.low or event.price
    open_trade["highest_price"] = max(float(open_trade["highest_price"]), high)
    open_trade["lowest_price"] = min(float(open_trade["lowest_price"]), low)


def _closed_trade_report(
    open_trade: dict[str, float | int | str],
    event: PriceEvent,
    exit_step: int,
    exit_price: float,
    pnl: float,
    reason: str | None,
) -> dict[str, float | int | str]:
    entry_price = float(open_trade["entry_price"])
    highest = float(open_trade["highest_price"])
    lowest = float(open_trade["lowest_price"])
    return {
        "entry_time": str(open_trade["entry_time"]),
        "exit_time": event.timestamp.isoformat(),
        "entry_step": int(open_trade["entry_step"]),
        "exit_step": exit_step,
        "holding_bars": exit_step - int(open_trade["entry_step"]),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": float(open_trade["quantity"]),
        "pnl": pnl,
        "return": pnl / max(entry_price * float(open_trade["quantity"]), 1e-12),
        "mfe": (highest - entry_price) / entry_price if entry_price > 0 else 0.0,
        "mae": (lowest - entry_price) / entry_price if entry_price > 0 else 0.0,
        "entry_reason": str(open_trade["entry_reason"]),
        "exit_reason": reason or "EXIT",
    }


def _exit_reason(config: BacktestConfig, open_trade: dict[str, float | int | str], event: PriceEvent, step: int) -> str | None:
    entry_price = float(open_trade["entry_price"])
    highest = max(float(open_trade["highest_price"]), event.high or event.price)
    low = event.low or event.price
    close = event.price

    if config.stop_loss_pct > 0 and low <= entry_price * (1.0 - config.stop_loss_pct / 100.0):
        return "STOP_LOSS"
    if config.take_profit_pct > 0 and (event.high or close) >= entry_price * (1.0 + config.take_profit_pct / 100.0):
        return "TAKE_PROFIT"
    if config.trailing_stop_pct > 0 and close <= highest * (1.0 - config.trailing_stop_pct / 100.0):
        return "TRAILING_STOP"
    if config.time_stop_bars > 0 and step - int(open_trade["entry_step"]) >= config.time_stop_bars:
        return "TIME_STOP"
    return None


def run_backtest(config: BacktestConfig) -> dict[str, float | int | str | list[float] | dict[str, float]]:
    events = _load_events_from_db(config)
    if not events:
        raise ValueError("No candles found in price_history for the selected backtest range.")

    manager = OrderManager(
        initial_balance=config.initial_balance,
        persist_to_db=False,
        fee_rate_bps=config.fee_rate_bps,
        sell_tax_bps=config.sell_tax_bps,
        slippage_bps=config.slippage_bps,
        market_rules=MarketRules(
            lot_size=config.lot_size,
            price_tick=config.price_tick,
            price_band_percent=config.price_band_percent,
            settlement_bars=config.settlement_bars,
            enforce_trading_session=config.enforce_trading_session,
        ),
    )
    strategy = RuleBasedStrategy(
        manager,
        max_balance_risk=config.max_balance_risk,
        min_trade_notional=config.min_trade_notional,
        allow_short=config.allow_short,
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
    price_history: list[float] = []
    open_trade: dict[str, float | int | str] | None = None
    trade_reports: list[dict[str, float | int | str]] = []

    with _muted_backtest_logs(config.verbose):
        for step, event in enumerate(events):
            price_history.append(event.price)
            if open_trade is not None:
                _update_open_trade(open_trade, event)
                reason = _exit_reason(config, open_trade, event, step)
                if reason:
                    position = manager.get_position(config.symbol)
                    if position.quantity > 0:
                        before_orders = len(manager.order_history)
                        order = manager.execute_order(
                            "SELL",
                            config.symbol,
                            position.quantity,
                            event.price,
                            timestamp=event.timestamp,
                            reason=reason,
                            current_step=step,
                            reference_price=event.open,
                        )
                        if order.status == "FILLED":
                            trade_reports.append(
                                _closed_trade_report(
                                    open_trade,
                                    event,
                                    step,
                                    order.price,
                                    float(order.trade_pnl or 0.0),
                                    order.reason,
                                )
                            )
                            open_trade = None

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

            if config.enable_regime_filter and not _regime_allows_buy(price_history, config.regime_ma_window):
                alerts = [alert for alert in alerts if alert.direction != "BUY"]

            before_position = manager.get_position(config.symbol).quantity
            before_orders = len(manager.order_history)
            strategy.execute(alerts, event, current_step=step)
            new_orders = manager.order_history[before_orders:]
            after_position = manager.get_position(config.symbol).quantity

            if before_position <= 0 < after_position and open_trade is None:
                filled_buy = next((order for order in new_orders if order.status == "FILLED" and order.side == "BUY"), None)
                position = manager.get_position(config.symbol)
                open_trade = _new_open_trade(
                    event,
                    position.quantity,
                    position.avg_entry,
                    step,
                    filled_buy.reason if filled_buy else "ENTRY",
                )
            elif open_trade is not None and after_position > before_position > 0:
                position = manager.get_position(config.symbol)
                open_trade["quantity"] = position.quantity
                open_trade["entry_price"] = position.avg_entry
            elif open_trade is not None and before_position > 0:
                sell_order = next((order for order in new_orders if order.status == "FILLED" and order.side == "SELL"), None)
                if sell_order:
                    report_source = dict(open_trade)
                    report_source["quantity"] = min(float(open_trade["quantity"]), sell_order.quantity)
                    trade_reports.append(
                        _closed_trade_report(
                            report_source,
                            event,
                            step,
                            sell_order.price,
                            float(sell_order.trade_pnl or 0.0),
                            sell_order.reason,
                        )
                    )
                    if after_position <= 0:
                        open_trade = None
                    else:
                        position = manager.get_position(config.symbol)
                        open_trade["quantity"] = position.quantity
                        open_trade["entry_price"] = position.avg_entry

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
    profit_factor = _profit_factor(trade_reports)
    turnover = (
        sum(order.quantity * order.price for order in manager.order_history if order.status == "FILLED")
        / max(config.initial_balance, 1e-12)
    )

    objective_score = (
        total_return
        - max_drawdown * 1.0
        + sharpe_ratio * 0.1
        + min(profit_factor, 5.0) * 0.02
        + win_rate * 0.03
        - turnover * 0.002
    )

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
        "profit_factor": profit_factor,
        "turnover": turnover,
        "total_fees": float(final_summary["total_fees"]),
        "total_taxes": float(final_summary["total_taxes"]),
        "total_slippage": float(final_summary["total_slippage"]),
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
            "allow_short": config.allow_short,
            "fee_rate_bps": config.fee_rate_bps,
            "sell_tax_bps": config.sell_tax_bps,
            "slippage_bps": config.slippage_bps,
            "lot_size": config.lot_size,
            "price_tick": config.price_tick,
            "price_band_percent": config.price_band_percent,
            "settlement_bars": config.settlement_bars,
            "enforce_trading_session": config.enforce_trading_session,
            "enable_regime_filter": config.enable_regime_filter,
            "regime_ma_window": config.regime_ma_window,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "trailing_stop_pct": config.trailing_stop_pct,
            "time_stop_bars": config.time_stop_bars,
        },
        "trade_reports": trade_reports,
        "equity_curve": equity_curve,
    }


def optimize_backtest(
    base_config: BacktestConfig,
    search_space: dict[str, list[int | float | bool]],
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


def walk_forward_backtest(
    base_config: BacktestConfig,
    search_space: dict[str, list[int | float | bool]],
    train_size: int,
    validation_size: int,
    step_size: int | None = None,
) -> dict[str, object]:
    """Optimize on rolling train windows and validate on unseen future windows."""
    if train_size <= 0 or validation_size <= 0:
        raise ValueError("train_size and validation_size must be positive.")

    rows = _load_raw_rows(base_config.db_path, base_config.start, base_config.end)
    if len(rows) < train_size + validation_size:
        raise ValueError("Not enough candles for the requested walk-forward windows.")

    step = step_size or validation_size
    windows = []
    start_index = 0
    while start_index + train_size + validation_size <= len(rows):
        train_start = rows[start_index]["timestamp"]
        train_end = rows[start_index + train_size - 1]["timestamp"]
        validation_start = rows[start_index + train_size]["timestamp"]
        validation_end = rows[start_index + train_size + validation_size - 1]["timestamp"]

        train_config = BacktestConfig(**asdict(base_config))
        train_config.start = train_start
        train_config.end = train_end
        optimization = optimize_backtest(train_config, search_space)

        best_params = dict(optimization["best_result"]["params"])
        validation_config = BacktestConfig(**asdict(base_config))
        validation_config.start = validation_start
        validation_config.end = validation_end
        for name, value in best_params.items():
            if hasattr(validation_config, name):
                setattr(validation_config, name, value)

        validation_result = run_backtest(validation_config)
        windows.append(
            {
                "train_start": train_start,
                "train_end": train_end,
                "validation_start": validation_start,
                "validation_end": validation_end,
                "best_train_result": optimization["best_result"],
                "validation_result": validation_result,
            }
        )
        start_index += step

    validation_returns = [float(window["validation_result"]["total_return"]) for window in windows]
    validation_drawdowns = [float(window["validation_result"]["max_drawdown"]) for window in windows]
    validation_objectives = [float(window["validation_result"]["objective_score"]) for window in windows]

    return {
        "windows": windows,
        "window_count": len(windows),
        "average_validation_return": fmean(validation_returns) if validation_returns else 0.0,
        "compound_validation_return": math.prod(1.0 + value for value in validation_returns) - 1.0,
        "max_validation_drawdown": max(validation_drawdowns) if validation_drawdowns else 0.0,
        "average_validation_objective": fmean(validation_objectives) if validation_objectives else 0.0,
    }
