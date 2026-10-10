from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENGINE_ROOT = Path(r"D:\PycharmProjects\IBKR_TradingBot\statistic\spx_adx_di_5min_2024_02_03")

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

import manual_regime_rules_backtest as rules  # noqa: E402


def load_symbols(config_path: Path, basket_name: str | None = None) -> list[str]:
    data = json.loads(config_path.read_text(encoding="utf-8"))
    target_name = basket_name or data.get("default_basket")
    for basket in data["baskets"]:
        if basket["name"] == target_name:
            return [str(symbol).upper() for symbol in basket["symbols"]]
    raise ValueError(f"Basket not found: {target_name}")


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity - running_max
    return float(drawdown.min())


def summarize(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            [
                {
                    "scope": "all",
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "total_points": 0.0,
                    "avg_points": 0.0,
                    "profit_factor": None,
                    "max_drawdown_points": 0.0,
                }
            ]
        )

    rows: list[dict[str, object]] = []
    group_specs: list[tuple[str, list[str]]] = [
        ("all", []),
        ("side", ["side"]),
        ("symbol", ["symbol"]),
        ("rule", ["rule"]),
        ("exit_reason", ["exit_reason"]),
    ]
    for scope, columns in group_specs:
        grouped = [((), trades)] if not columns else trades.groupby(columns, dropna=False, sort=True)
        for key, frame in grouped:
            points = pd.to_numeric(frame["weighted_points"], errors="coerce").fillna(0.0)
            wins = int((points > 0).sum())
            losses = int((points < 0).sum())
            gross_profit = float(points[points > 0].sum())
            gross_loss = float(points[points < 0].sum())
            row: dict[str, object] = {
                "scope": scope,
                "trades": int(len(frame)),
                "wins": wins,
                "losses": losses,
                "win_rate": float(wins / len(frame)) if len(frame) else 0.0,
                "total_points": float(points.sum()),
                "avg_points": float(points.mean()) if len(frame) else 0.0,
                "profit_factor": gross_profit / abs(gross_loss) if gross_loss < 0 else None,
                "max_drawdown_points": max_drawdown(points.cumsum()),
            }
            if columns:
                if not isinstance(key, tuple):
                    key = (key,)
                for column, value in zip(columns, key):
                    row[column] = value
            rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Simulate all engine trades for the configured basket in 2026.")
    parser.add_argument("--config", default=str(PROJECT_ROOT / "configs" / "baskets_v1.json"))
    parser.add_argument("--basket", default="")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end-exclusive", default="2026-07-01")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "runs" / "engine_trade_simulation_2026.csv"))
    parser.add_argument("--summary-output", default=str(PROJECT_ROOT / "runs" / "engine_trade_simulation_2026_summary.csv"))
    args = parser.parse_args()

    symbols = load_symbols(Path(args.config), args.basket or None)
    frames: list[pd.DataFrame] = []
    errors: list[dict[str, str]] = []

    for symbol in symbols:
        try:
            _, trades, _ = rules.run_backtest(
                symbol,
                args.start,
                args.end_exclusive,
                structure_lookback=10,
                bull_continuation_mode="combo",
                learn_long_mode="off",
                learn_long_window=5,
                learn_long_min_trades=3,
                learn_long_min_win_rate=0.45,
                learn_long_cooldown_days=15,
            )
        except Exception as exc:
            errors.append({"symbol": symbol, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if trades.empty:
            continue
        frame = trades.copy()
        frame["symbol"] = symbol
        frame["entry_date"] = pd.to_datetime(frame["entry_date"], errors="coerce")
        frame["exit_date"] = pd.to_datetime(frame["exit_date"], errors="coerce")
        frame = frame[
            (frame["entry_date"] >= pd.Timestamp("2026-01-01"))
            & (frame["entry_date"] < pd.Timestamp(args.end_exclusive))
        ].copy()
        if not frame.empty:
            frames.append(frame)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path = Path(args.summary_output)
    summary_path.parent.mkdir(parents=True, exist_ok=True)

    if frames:
        all_trades = pd.concat(frames, ignore_index=True, sort=False)
        sort_columns = [column for column in ["entry_date", "symbol", "track", "rule"] if column in all_trades.columns]
        all_trades = all_trades.sort_values(sort_columns, kind="mergesort").reset_index(drop=True)
    else:
        all_trades = pd.DataFrame()

    all_trades.to_csv(output_path, index=False)
    summarize(all_trades).to_csv(summary_path, index=False)

    if errors:
        pd.DataFrame(errors).to_csv(output_path.with_name(output_path.stem + "_errors.csv"), index=False)

    print(f"symbols={len(symbols)}")
    print(f"symbols_with_errors={len(errors)}")
    print(f"trades={len(all_trades)}")
    if not all_trades.empty:
        points = pd.to_numeric(all_trades["weighted_points"], errors="coerce").fillna(0.0)
        print(f"total_weighted_points={points.sum():.2f}")
        print(f"win_rate={(points > 0).mean():.4f}")
    print(f"output={output_path}")
    print(f"summary={summary_path}")


if __name__ == "__main__":
    main()
