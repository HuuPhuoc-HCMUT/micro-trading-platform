import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backtesting.engine import BacktestConfig, run_backtest, walk_forward_backtest


def write_price_history(db_path: Path, closes: list[float], volumes: list[float] | None = None) -> None:
    if volumes is None:
        volumes = [10.0] * len(closes)

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE price_history (
                timestamp TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL
            )
            """
        )
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        rows = []
        for index, close in enumerate(closes):
            previous = closes[index - 1] if index else close
            high = max(previous, close) * 1.001
            low = min(previous, close) * 0.999
            rows.append(
                (
                    (start + timedelta(minutes=index)).isoformat(),
                    previous,
                    high,
                    low,
                    close,
                    volumes[index],
                )
            )
        conn.executemany(
            "INSERT INTO price_history(timestamp, open, high, low, close, volume) VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )


def test_backtest_stays_flat_on_noisy_sideways_market(tmp_path: Path) -> None:
    db_path = tmp_path / "sideways.db"
    closes = [100.0 + (0.03 if index % 2 else -0.03) for index in range(80)]
    write_price_history(db_path, closes)

    result = run_backtest(
        BacktestConfig(
            db_path=str(db_path),
            symbol="TEST",
            ma_short_window=5,
            ma_long_window=15,
            probability_min_history=10,
            probability_signal_threshold=0.20,
            min_trade_notional=10.0,
            allow_short=False,
        )
    )

    assert result["candles"] == len(closes)
    assert result["orders"] <= 2
    assert abs(result["total_return"]) < 0.002
    assert result["max_drawdown"] < 0.002


def test_backtest_can_participate_in_confirmed_uptrend(tmp_path: Path) -> None:
    db_path = tmp_path / "uptrend.db"
    closes = [100.0 + index * 0.45 for index in range(90)]
    volumes = [10.0 + index * 0.05 for index in range(90)]
    write_price_history(db_path, closes, volumes)

    result = run_backtest(
        BacktestConfig(
            db_path=str(db_path),
            symbol="TEST",
            ma_short_window=5,
            ma_long_window=15,
            probability_min_history=10,
            probability_signal_threshold=0.08,
            min_trade_notional=10.0,
            max_balance_risk=0.12,
            allow_short=False,
        )
    )

    assert result["candles"] == len(closes)
    assert result["orders"] > 0
    assert result["ending_equity"] >= result["initial_balance"]
    assert result["max_drawdown"] < 0.01


def test_backtest_does_not_open_short_by_default_in_downtrend(tmp_path: Path) -> None:
    db_path = tmp_path / "downtrend.db"
    closes = [120.0 - index * 0.35 for index in range(90)]
    write_price_history(db_path, closes)

    result = run_backtest(
        BacktestConfig(
            db_path=str(db_path),
            symbol="TEST",
            ma_short_window=5,
            ma_long_window=15,
            probability_min_history=10,
            probability_signal_threshold=0.08,
            min_trade_notional=10.0,
            allow_short=False,
        )
    )

    assert result["orders"] == 0
    assert result["ending_equity"] == result["initial_balance"]


def test_backtest_exit_engine_records_trade_report(tmp_path: Path) -> None:
    db_path = tmp_path / "exit_report.db"
    closes = [100.0 + index * 0.8 for index in range(35)] + [128.0 - index * 1.4 for index in range(20)]
    write_price_history(db_path, closes)

    result = run_backtest(
        BacktestConfig(
            db_path=str(db_path),
            symbol="TEST",
            ma_short_window=4,
            ma_long_window=10,
            probability_min_history=8,
            probability_signal_threshold=0.08,
            min_trade_notional=10.0,
            max_balance_risk=0.12,
            stop_loss_pct=3.0,
            trailing_stop_pct=2.0,
            time_stop_bars=20,
            allow_short=False,
        )
    )

    assert "trade_reports" in result
    assert result["closed_trades"] >= 1
    assert len(result["trade_reports"]) >= 1
    trade = result["trade_reports"][0]
    assert "entry_price" in trade
    assert "exit_price" in trade
    assert "mfe" in trade
    assert "mae" in trade
    assert trade["exit_reason"] in {"STOP_LOSS", "TRAILING_STOP", "TIME_STOP", "SIGNAL_EXIT_OR_SHORT"}


def test_walk_forward_backtest_validates_on_future_windows(tmp_path: Path) -> None:
    db_path = tmp_path / "walk_forward.db"
    closes = [100.0 + index * 0.25 + (0.15 if index % 7 == 0 else 0.0) for index in range(120)]
    write_price_history(db_path, closes)

    result = walk_forward_backtest(
        BacktestConfig(
            db_path=str(db_path),
            symbol="TEST",
            probability_min_history=8,
            min_trade_notional=10.0,
            allow_short=False,
        ),
        search_space={
            "ma_short_window": [4, 6],
            "ma_long_window": [12, 16],
            "probability_signal_threshold": [0.08, 0.12],
            "enable_regime_filter": [False, True],
        },
        train_size=50,
        validation_size=20,
    )

    assert result["window_count"] > 0
    assert len(result["windows"]) == result["window_count"]
    assert "average_validation_return" in result
    assert "compound_validation_return" in result
