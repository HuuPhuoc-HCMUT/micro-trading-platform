import argparse
import json

from backtesting.engine import BacktestConfig, optimize_backtest, run_backtest, walk_forward_backtest


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest and optimize the trading strategy.")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--db-path", default="trading_platform.db")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--initial-balance", type=float, default=10000.0)
    parser.add_argument("--optimize", action="store_true")
    parser.add_argument("--walk-forward", action="store_true")
    parser.add_argument("--train-size", type=int, default=120)
    parser.add_argument("--validation-size", type=int, default=40)
    parser.add_argument("--top", type=int, default=3, help="How many top optimization results to print.")
    args = parser.parse_args()

    config = BacktestConfig(
        symbol=args.symbol,
        db_path=args.db_path,
        start=args.start,
        end=args.end,
        initial_balance=args.initial_balance,
    )

    search_space = {
        "ma_short_window": [8, 15],
        "ma_long_window": [30, 60],
        "spike_threshold_percent": [1.5, 2.5],
        "volume_multiplier": [2.0, 3.0],
        "probability_signal_threshold": [0.14, 0.22],
        "max_balance_risk": [0.10, 0.18],
        "enable_regime_filter": [False, True],
        "stop_loss_pct": [0.0, 3.0],
        "trailing_stop_pct": [0.0],
        "time_stop_bars": [0],
    }

    if args.walk_forward:
        result = walk_forward_backtest(
            config,
            search_space,
            train_size=args.train_size,
            validation_size=args.validation_size,
        )
        print(json.dumps(result, indent=2))
        return

    if args.optimize:
        optimization = optimize_backtest(config, search_space)
        ranked = sorted(
            optimization["results"],
            key=lambda item: float(item["objective_score"]),
            reverse=True,
        )
        payload = {
            "best_result": optimization["best_result"],
            "top_results": ranked[: max(args.top, 1)],
            "tested_configs": len(ranked),
        }
        print(json.dumps(payload, indent=2))
        return

    result = run_backtest(config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
