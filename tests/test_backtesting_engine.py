from backtesting.engine import BacktestConfig, optimize_backtest, run_backtest


def test_run_backtest_returns_core_metrics() -> None:
    config = BacktestConfig(
        db_path="trading_platform.db",
        symbol="BTC/USDT",
        ma_short_window=5,
        ma_long_window=10,
        probability_min_history=5,
        probability_signal_threshold=0.08,
        min_trade_notional=10.0,
        max_balance_risk=0.12,
    )

    result = run_backtest(config)

    assert result["candles"] > 0
    assert "objective_score" in result
    assert "equity_curve" in result
    assert len(result["equity_curve"]) == result["candles"]


def test_optimize_backtest_picks_best_result() -> None:
    config = BacktestConfig(
        db_path="trading_platform.db",
        symbol="BTC/USDT",
        probability_min_history=5,
        min_trade_notional=10.0,
    )
    search_space = {
        "ma_short_window": [5, 8],
        "ma_long_window": [10, 15],
        "probability_signal_threshold": [0.08, 0.12],
    }

    result = optimize_backtest(config, search_space)

    assert len(result["results"]) == 8
    assert result["best_result"]["objective_score"] == max(
        item["objective_score"] for item in result["results"]
    )
